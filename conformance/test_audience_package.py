from __future__ import annotations

import copy
from datetime import datetime, timezone
import io
import json
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import unittest
from unittest import mock
import warnings
import zipfile


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "skills" / "audience-ad-testing-lab" / "scripts"
FIXTURES = ROOT / "conformance" / "fixtures" / "audience-research"
sys.path.insert(0, str(SCRIPTS))

from audience_lab.audience_package import (  # noqa: E402
    PackageSafetyError,
    PackageValidationError,
    build_audience_package,
    read_validated_package_archive,
    validate_package_archive,
)
from audience_lab import audience_package  # noqa: E402


class AudiencePackageTest(unittest.TestCase):
    def setUp(self) -> None:
        self.brief = json.loads((FIXTURES / "approved-brief.json").read_text())
        self.panel = json.loads((FIXTURES / "approved-panel.json").read_text())

    def _build(self, directory: Path, brief=None, panel=None, *, now=None):
        return build_audience_package(
            self.brief if brief is None else brief,
            self.panel if panel is None else panel,
            directory,
            generator_version="1.0.0",
            now=now,
        )

    def test_build_is_byte_deterministic_and_archive_validates(self) -> None:
        with tempfile.TemporaryDirectory() as first, tempfile.TemporaryDirectory() as second:
            one = self._build(Path(first))
            def reverse_objects(value):
                if isinstance(value, dict):
                    return {key: reverse_objects(value[key]) for key in reversed(value)}
                if isinstance(value, list):
                    return [reverse_objects(item) for item in value]
                return value
            two = self._build(Path(second), reverse_objects(self.brief), reverse_objects(self.panel))
            self.assertEqual(one.package_zip_sha256, two.package_zip_sha256)
            self.assertEqual(
                "62b38b8a7f7265c89682627f8f30a3ccf9ab0fc8142227389de2aaeca5609f5e",
                one.package_zip_sha256,
            )
            self.assertEqual(one.package_zip_path.read_bytes(), two.package_zip_path.read_bytes())
            result = validate_package_archive(one.package_zip_path)
            self.assertEqual(result["panel_id"], self.panel["panel_id"])
            self.assertEqual(result["panel_version"], self.panel["version"])
            with zipfile.ZipFile(one.package_zip_path) as archive:
                self.assertEqual(
                    archive.namelist(),
                    [
                        "persona-research-brief.json", "saved-audience-panel.json",
                        "research-sources.csv", "audience-research-report.html",
                        "README.txt", "package-manifest.json",
                    ],
                )
                for info in archive.infolist():
                    self.assertEqual(info.date_time, (1980, 1, 1, 0, 0, 0))
                    self.assertEqual(info.compress_type, zipfile.ZIP_STORED)
                    self.assertEqual((info.external_attr >> 16) & 0o777, 0o600)
                manifest = json.loads(archive.read("package-manifest.json"))
                self.assertNotIn("package-manifest.json", manifest["files"])
                self.assertNotIn("audience-panel-package.zip", manifest["files"])

    def test_public_validated_archive_reader_uses_one_exact_snapshot(self) -> None:
        class OneReadStream(io.BytesIO):
            reads = 0

            def read(self, *args, **kwargs):
                self.reads += 1
                if self.reads > 1:
                    raise AssertionError("archive source was read more than once")
                return super().read(*args, **kwargs)

        with tempfile.TemporaryDirectory() as temp:
            result = self._build(Path(temp))
            raw = result.package_zip_path.read_bytes()
            stream = OneReadStream(raw)
            snapshot = read_validated_package_archive(stream)
            self.assertEqual(1, stream.reads)
            self.assertEqual(
                {"archive_bytes", "validation", "members"},
                set(snapshot),
            )
            self.assertEqual(raw, snapshot["archive_bytes"])
            self.assertEqual(
                validate_package_archive(raw),
                snapshot["validation"],
            )
            with zipfile.ZipFile(io.BytesIO(raw)) as archive:
                self.assertEqual(
                    {
                        name: archive.read(name)
                        for name in archive.namelist()
                    },
                    snapshot["members"],
                )

    def test_html_escapes_content_and_links_only_http(self) -> None:
        brief = copy.deepcopy(self.brief)
        panel = copy.deepcopy(self.panel)
        hostile = "</script><img src=https://evil.example/x onerror=alert(1)> & insight"
        brief["findings"][0]["statement"] = hostile
        panel["segments"][0]["description"] = hostile
        with tempfile.TemporaryDirectory() as temp:
            result = self._build(Path(temp), brief, panel)
            html = result.report_path.read_text()
            self.assertNotIn("<img", html)
            self.assertNotIn("</script>", html.lower())
            self.assertIn("&lt;img", html)
            self.assertIn('href="https://example.com/research/workflow-adoption"', html)
            self.assertNotRegex(html, r"(?i)<(?:script|img|iframe|video|audio|link|form)\b")

    def test_csv_is_rfc4180_and_formula_safe(self) -> None:
        brief = copy.deepcopy(self.brief)
        brief["evidence_sources"][0]["source_label"] = "=HYPERLINK(\"https://bad\")"
        with tempfile.TemporaryDirectory() as temp:
            result = self._build(Path(temp), brief, self.panel)
            data = result.sources_csv_path.read_bytes()
            self.assertIn(b"\r\n", data)
            self.assertNotIn(b"\n", data.replace(b"\r\n", b""))
            self.assertIn(b"'=HYPERLINK", data)

    def test_provisional_package_has_header_only_sources_and_clear_label(self) -> None:
        brief = copy.deepcopy(self.brief)
        panel = copy.deepcopy(self.panel)
        brief.update(status="provisional_no_research", research_mode="provisional_no_research")
        brief["evidence_sources"] = []
        brief["findings"] = []
        brief["research_questions"] = []
        brief["coverage"] = {key: "empty" for key in brief["coverage"]}
        brief["segment_hypotheses"][0].update(
            origin="provisional_user_defined", finding_ids=[], evidence_ids=[], confidence="low"
        )
        panel["segments"][0].update(
            origin="provisional_user_defined", finding_ids=[], evidence_ids=[],
            weight_source_evidence=[], weighting_rule="planning_allocation",
        )
        panel["persona_archetypes"][0].update(finding_ids=[], evidence_ids=[], evidence_strength="low")
        for dimension in panel["context_strata"][0]["dimensions"]:
            dimension.update(status="experimental", source_evidence=[], finding_ids=[])
        for provenance in panel["grounded_context_profiles"][0]["context_attribute_provenance"]:
            provenance.update(status="experimental", source_evidence=[], finding_ids=[])
        panel["persona_research"].update(
            mode="provisional_no_research", status="provisional_no_research", expires_at="2026-07-30T12:00:00Z",
            source_types=[], evidence_ids=[], source_state="no_research_sources", coverage=brief["coverage"],
        )
        with tempfile.TemporaryDirectory() as temp:
            result = self._build(
                Path(temp),
                brief,
                panel,
                now=datetime(2026, 7, 22, 12, 0, tzinfo=timezone.utc),
            )
            self.assertEqual(result.sources_csv_path.read_text().count("\n"), 1)
            self.assertIn("Provisional — no research sources", result.report_path.read_text())

    def test_tampering_and_hostile_archives_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            result = self._build(Path(temp))
            members = {}
            with zipfile.ZipFile(result.package_zip_path) as source:
                for name in source.namelist():
                    members[name] = source.read(name)
            members["saved-audience-panel.json"] += b" "
            tampered = Path(temp) / "tampered.zip"
            with zipfile.ZipFile(tampered, "w", compression=zipfile.ZIP_STORED) as archive:
                for name, data in members.items():
                    archive.writestr(name, data)
            with self.assertRaises(PackageValidationError):
                validate_package_archive(tampered)
            with self.assertRaises(PackageValidationError):
                read_validated_package_archive(tampered)

            nonfinite = Path(temp) / "nonfinite.zip"
            nonfinite_members = dict(members)
            nonfinite_members["saved-audience-panel.json"] = nonfinite_members["saved-audience-panel.json"].replace(
                b'"study_weight":1.0', b'"study_weight":NaN'
            )
            with zipfile.ZipFile(nonfinite, "w", compression=zipfile.ZIP_STORED) as archive:
                for name, data in nonfinite_members.items():
                    archive.writestr(name, data)
            with self.assertRaises(PackageValidationError):
                validate_package_archive(nonfinite)

            traversal = Path(temp) / "traversal.zip"
            with zipfile.ZipFile(traversal, "w") as archive:
                archive.writestr("../escape", b"x")
            with self.assertRaises(PackageSafetyError):
                validate_package_archive(traversal)
            with self.assertRaises(PackageSafetyError):
                read_validated_package_archive(traversal)

    def test_duplicate_symlink_and_compression_bomb_entries_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            result = self._build(Path(temp) / "built")
            with zipfile.ZipFile(result.package_zip_path) as source:
                members = [(info, source.read(info)) for info in source.infolist()]

            duplicate = Path(temp) / "duplicate.zip"
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", UserWarning)
                with zipfile.ZipFile(duplicate, "w") as archive:
                    for info, data in members:
                        archive.writestr(info.filename, data)
                    archive.writestr("README.txt", b"duplicate")
            with self.assertRaises(PackageSafetyError):
                validate_package_archive(duplicate)

            symlink = Path(temp) / "symlink.zip"
            with zipfile.ZipFile(symlink, "w") as archive:
                for info, data in members:
                    copied = zipfile.ZipInfo(info.filename)
                    copied.create_system = 3
                    copied.external_attr = ((stat.S_IFLNK | 0o777) if info.filename == "README.txt" else (stat.S_IFREG | 0o600)) << 16
                    archive.writestr(copied, data)
            with self.assertRaises(PackageSafetyError):
                validate_package_archive(symlink)

            bomb = Path(temp) / "bomb.zip"
            with zipfile.ZipFile(bomb, "w", compression=zipfile.ZIP_DEFLATED) as archive:
                for info, data in members:
                    archive.writestr(info.filename, b"A" * 1_000_000 if info.filename == "README.txt" else data)
            with self.assertRaises(PackageSafetyError):
                validate_package_archive(bomb)

    def test_external_report_assets_are_rejected_before_output(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            template = Path(temp) / "bad-template.html"
            template.write_text('<!doctype html><html><body><img src="https://evil.example/x">{{REPORT_BODY}}</body></html>')
            output = Path(temp) / "output"
            with mock.patch.object(audience_package, "_template_path", return_value=template):
                with self.assertRaises(PackageSafetyError):
                    self._build(output)
            self.assertFalse(output.exists())

    def test_remote_css_asset_forms_are_rejected(self) -> None:
        cases = (
            '<style>.ad{background-image:image-set("https://evil.example/x" 1x)}</style>',
            '<div style="background:image-set(//evil.example/x 1x)">x</div>',
            '<style>.ad{background-image:image-set("data:image/png;base64,abc" 1x)}</style>',
        )
        for css in cases:
            with self.subTest(css=css), tempfile.TemporaryDirectory() as temp:
                template = Path(temp) / "bad-template.html"
                template.write_text(f'<!doctype html><html><body>{css}{{{{REPORT_BODY}}}}</body></html>')
                with mock.patch.object(audience_package, "_template_path", return_value=template):
                    with self.assertRaises(PackageSafetyError):
                        self._build(Path(temp) / "output")

    def test_existing_output_symlink_ancestor_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            outside = root / "outside"
            outside.mkdir()
            link = root / "linked-parent"
            link.symlink_to(outside, target_is_directory=True)
            with self.assertRaises(PackageSafetyError):
                self._build(link / "package")
            self.assertEqual(list(outside.iterdir()), [])

    def test_generator_version_dispatch_preserves_v1_and_rejects_unknown(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            result = self._build(root / "built")
            old_bytes = result.package_zip_path.read_bytes()
            changed_template = root / "future-template.html"
            changed_template.write_text('<!doctype html><html><body>future{{REPORT_BODY}}</body></html>')
            with mock.patch.object(audience_package, "_template_path", return_value=changed_template):
                self.assertEqual(validate_package_archive(old_bytes)["status"], "valid")

            with zipfile.ZipFile(io.BytesIO(old_bytes)) as source:
                members = {name: source.read(name) for name in source.namelist()}
            manifest = json.loads(members["package-manifest.json"])
            manifest["generator_version"] = "9.0.0"
            members["package-manifest.json"] = (
                json.dumps(manifest, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
            ).encode()
            unknown = root / "unknown-version.zip"
            with zipfile.ZipFile(unknown, "w", compression=zipfile.ZIP_STORED) as archive:
                for name in audience_package.ARCHIVE_FILES:
                    archive.writestr(name, members[name])
            with self.assertRaisesRegex(PackageValidationError, "unsupported generator_version"):
                validate_package_archive(unknown)
            with self.assertRaisesRegex(PackageValidationError, "unsupported generator_version"):
                build_audience_package(self.brief, self.panel, root / "unsupported", generator_version="9.0.0")

    def test_private_safe_extract_supports_binary_stream_and_allowed_root(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            result = self._build(root / "built")
            destination = root / "snapshots" / "panel"
            validation = audience_package._safe_extract_package_archive(
                io.BytesIO(result.package_zip_path.read_bytes()), destination, allowed_root=root / "snapshots"
            )
            self.assertEqual(validation["status"], "valid")
            self.assertEqual(stat.S_IMODE(destination.stat().st_mode), 0o700)
            self.assertEqual(set(path.name for path in destination.iterdir()), set(audience_package.ARCHIVE_FILES))
            with self.assertRaises(PackageSafetyError):
                audience_package._safe_extract_package_archive(
                    result.package_zip_path, root / "outside", allowed_root=root / "snapshots"
                )

    def test_cli_emits_one_json_object_and_secure_files(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp) / "package"
            completed = subprocess.run(
                [sys.executable, str(SCRIPTS / "build-audience-package.py"),
                 "--brief", str(FIXTURES / "approved-brief.json"),
                 "--panel", str(FIXTURES / "approved-panel.json"),
                 "--output-dir", str(output), "--generator-version", "1.0.0"],
                text=True, capture_output=True, check=False,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            payload = json.loads(completed.stdout)
            self.assertEqual(payload["status"], "built")
            self.assertEqual(stat.S_IMODE(output.stat().st_mode), 0o700)
            for path in output.iterdir():
                self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)

    def test_validation_failure_is_json_and_leaves_no_partial_output(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            invalid = copy.deepcopy(self.panel)
            invalid["panel_id"] = "../escape"
            panel_path = root / "invalid-panel.json"
            panel_path.write_text(json.dumps(invalid), encoding="utf-8")
            output = root / "output"
            completed = subprocess.run(
                [sys.executable, str(SCRIPTS / "build-audience-package.py"),
                 "--brief", str(FIXTURES / "approved-brief.json"),
                 "--panel", str(panel_path), "--output-dir", str(output)],
                text=True, capture_output=True, check=False,
            )
            self.assertEqual(completed.returncode, 2)
            self.assertEqual(json.loads(completed.stdout)["error"], "validation")
            self.assertFalse(output.exists())

    def test_argparse_failures_emit_structured_json_only(self) -> None:
        cases = ([], ["--unknown-option"], ["--brief"])
        for args in cases:
            with self.subTest(args=args):
                completed = subprocess.run(
                    [sys.executable, str(SCRIPTS / "build-audience-package.py"), *args],
                    text=True, capture_output=True, check=False,
                )
                self.assertEqual(completed.returncode, 2)
                self.assertEqual(completed.stderr, "")
                payload = json.loads(completed.stdout)
                self.assertEqual(payload["status"], "error")
                self.assertEqual(payload["error"], "arguments")


if __name__ == "__main__":
    unittest.main()
