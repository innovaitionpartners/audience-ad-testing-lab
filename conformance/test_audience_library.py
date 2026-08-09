from __future__ import annotations

import copy
from datetime import datetime, timedelta, timezone
import io
import json
import os
from pathlib import Path
import socket
import stat
import subprocess
import sys
import tempfile
import time
import unittest
import zipfile


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "skills" / "audience-ad-testing-lab" / "scripts"
FIXTURES = ROOT / "conformance" / "fixtures" / "audience-research"
CLI = SCRIPTS / "manage-audience-library.py"
sys.path.insert(0, str(SCRIPTS))

from audience_lab.audience_library import (  # noqa: E402
    ImmutableVersionConflict,
    LibraryLock,
    LibraryLockError,
    LibraryNotFoundError,
    LibrarySafetyError,
    find_package,
    list_panels,
    register_package,
    resolve_library_root,
    show_panel,
)
from audience_lab.audience_package import build_audience_package  # noqa: E402
from audience_lab.audience_package import PackageValidationError  # noqa: E402


class AudienceLibraryTest(unittest.TestCase):
    def setUp(self) -> None:
        self.brief = json.loads((FIXTURES / "approved-brief.json").read_text())
        self.panel = json.loads((FIXTURES / "approved-panel.json").read_text())

    def _package(self, root: Path, *, panel=None):
        return build_audience_package(self.brief, self.panel if panel is None else panel, root)

    def test_register_list_show_and_lookup_are_minimal_sorted_and_private(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            library = base / "library"
            first = self._package(base / "package-1")
            result = register_package(first.package_zip_path, library_root=library)
            self.assertEqual(result["status"], "registered")
            index_before_replay = (library / "index.json").read_bytes()
            self.assertEqual(register_package(first.package_zip_path, library_root=library)["status"], "already_registered")
            self.assertEqual(
                index_before_replay,
                (library / "index.json").read_bytes(),
                "v2 identical registration must preserve the exact index bytes",
            )

            panel2 = copy.deepcopy(self.panel)
            panel2.update(panel_id="alpha-panel", panel_name="Alpha panel", version="2.0.0")
            brief2 = copy.deepcopy(self.brief)
            second = build_audience_package(brief2, panel2, base / "package-2")
            register_package(second.package_zip_path, library_root=library)

            listing = list_panels(library_root=library)
            self.assertEqual([x["panel_id"] for x in listing["panels"]], ["alpha-panel", "operations-leaders"])
            exposed = json.dumps(listing)
            self.assertNotIn(self.brief["findings"][0]["statement"], exposed)
            self.assertNotIn(self.brief["evidence_sources"][0]["source_label"], exposed)
            shown = show_panel("operations-leaders", "1.0.0", library_root=library)
            self.assertEqual(set(shown), {"status", "panel"})
            self.assertEqual(
                {
                    "panel_id", "panel_name", "version", "registered_at",
                    "package_manifest_sha256", "package_manifest_byte_count",
                    "package_zip_sha256", "package_zip_byte_count",
                    "relative_path",
                },
                set(shown["panel"]),
            )
            self.assertNotIn("findings", json.dumps(shown))
            package_path = find_package("operations-leaders", "1.0.0", library_root=library)
            self.assertEqual(package_path.name, "audience-panel-package.zip")
            for directory in (library, library / "panels", package_path.parent):
                self.assertEqual(stat.S_IMODE(directory.stat().st_mode), 0o700)
            for path in (library / "index.json", package_path, *package_path.parent.glob("*")):
                self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)

    def test_semantic_version_ordering(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            for version in ("1.10.0", "1.2.0", "2.0.0"):
                panel = copy.deepcopy(self.panel)
                panel["version"] = version
                package = self._package(base / ("p-" + version), panel=panel)
                register_package(package.package_zip_path, library_root=base / "library")
            versions = [x["version"] for x in list_panels(library_root=base / "library")["panels"]]
            self.assertEqual(versions, ["1.2.0", "1.10.0", "2.0.0"])

    def test_changed_content_conflicts_and_original_is_unchanged(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            original = self._package(base / "original")
            library = base / "library"
            register_package(original.package_zip_path, library_root=library)
            stored = find_package("operations-leaders", "1.0.0", library_root=library)
            before = stored.read_bytes()
            panel = copy.deepcopy(self.panel)
            panel["panel_name"] = "Changed without a version bump"
            changed = self._package(base / "changed", panel=panel)
            with self.assertRaises(ImmutableVersionConflict):
                register_package(changed.package_zip_path, library_root=library)
            self.assertEqual(stored.read_bytes(), before)

            alternate_buffer = io.BytesIO()
            with zipfile.ZipFile(io.BytesIO(original.package_zip_path.read_bytes())) as source_zip:
                with zipfile.ZipFile(alternate_buffer, "w", compression=zipfile.ZIP_STORED) as alternate_zip:
                    for info in reversed(source_zip.infolist()):
                        alternate_zip.writestr(info, source_zip.read(info.filename))
            alternate = base / "alternate-order.zip"
            alternate.write_bytes(alternate_buffer.getvalue())
            self.assertNotEqual(alternate.read_bytes(), original.package_zip_path.read_bytes())
            with self.assertRaises(ImmutableVersionConflict):
                register_package(alternate, library_root=library)
            self.assertEqual(stored.read_bytes(), before)

    def test_rejects_provisional_unsafe_ids_symlinks_and_path_escape(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            package = self._package(base / "approved")
            library = base / "library"
            for panel_id, version in (("../escape", "1.0.0"), ("Bad_ID", "1.0.0"), ("7panel", "1.0.0"), ("ok", "1.0")):
                with self.assertRaises(LibrarySafetyError):
                    find_package(panel_id, version, library_root=library)
            linked = base / "linked.zip"
            linked.symlink_to(package.package_zip_path)
            with self.assertRaises(LibrarySafetyError):
                register_package(linked, library_root=library)

            real_root_parent = base / "real-root-parent"
            real_root_parent.mkdir()
            root_alias = base / "root-alias"
            root_alias.symlink_to(real_root_parent, target_is_directory=True)
            with self.assertRaises(LibrarySafetyError):
                register_package(package.package_zip_path, library_root=root_alias / "library")

            real_source_parent = base / "real-source-parent"
            real_source_parent.mkdir()
            source_copy = real_source_parent / "package.zip"
            source_copy.write_bytes(package.package_zip_path.read_bytes())
            source_alias = base / "source-alias"
            source_alias.symlink_to(real_source_parent, target_is_directory=True)
            with self.assertRaises(LibrarySafetyError):
                register_package(source_alias / "package.zip", library_root=library)

            os.environ["AUDIENCE_LAB_LIBRARY_DIR"] = str(base / "env-library")
            try:
                self.assertEqual(resolve_library_root(), (base / "env-library").resolve())
            finally:
                del os.environ["AUDIENCE_LAB_LIBRARY_DIR"]

            register_package(package.package_zip_path, library_root=library)
            stored = find_package("operations-leaders", "1.0.0", library_root=library)
            outside = base / "outside.zip"
            outside.write_bytes(stored.read_bytes())
            stored.unlink()
            stored.symlink_to(outside)
            with self.assertRaises(LibrarySafetyError):
                find_package("operations-leaders", "1.0.0", library_root=library)

    def test_provisional_and_corrupt_packages_are_not_registered(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
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
            created_at = datetime.now(timezone.utc) - timedelta(days=1)
            expires_at = created_at + timedelta(days=30)
            panel["created_at"] = created_at.isoformat().replace("+00:00", "Z")
            panel["updated_at"] = panel["created_at"]
            panel["persona_research"].update(
                mode="provisional_no_research", status="provisional_no_research",
                expires_at=expires_at.isoformat().replace("+00:00", "Z"), source_types=[], evidence_ids=[],
                source_state="no_research_sources", coverage=brief["coverage"],
            )
            provisional = build_audience_package(brief, panel, base / "provisional")
            with self.assertRaises(PackageValidationError):
                register_package(provisional.package_zip_path, library_root=base / "library")
            corrupt = base / "corrupt.zip"
            corrupt.write_bytes(b"not a zip")
            with self.assertRaises(LibrarySafetyError):
                register_package(corrupt, library_root=base / "library")

    def test_missing_and_corrupt_index_or_package_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "library"
            with self.assertRaises(LibraryNotFoundError):
                show_panel("missing", "1.0.0", library_root=root)
            root.mkdir(mode=0o700)
            (root / "index.json").write_text('{"findings":["secret"]}')
            with self.assertRaises(LibrarySafetyError):
                list_panels(library_root=root)
            not_a_directory = Path(temp) / "library-file"
            not_a_directory.write_text("x")
            with self.assertRaises(LibrarySafetyError):
                list_panels(library_root=not_a_directory)

    def test_lookup_rejects_index_byte_count_mismatch(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            library = base / "library"
            package = self._package(base / "package")
            register_package(package.package_zip_path, library_root=library)
            index_path = library / "index.json"
            index = json.loads(index_path.read_text())
            index["panels"][0]["package_zip_byte_count"] += 1
            index_path.write_text(json.dumps(index))
            with self.assertRaises(LibrarySafetyError):
                find_package("operations-leaders", "1.0.0", library_root=library)

    def test_live_lock_times_out_and_stale_dead_local_lock_is_reclaimed(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "library"
            root.mkdir(mode=0o700)
            lock = root / "library.lock"
            lock.write_text(json.dumps({
                "pid": os.getpid(), "host": socket.gethostname(),
                "acquired_at": time.time() - 1000,
            }))
            with self.assertRaises(LibraryLockError):
                with LibraryLock(root, timeout_seconds=0.03, poll_seconds=0.005):
                    pass
            lock.write_text(json.dumps({
                "pid": 99999999, "host": socket.gethostname(),
                "acquired_at": time.time() - 601,
            }))
            with LibraryLock(root, timeout_seconds=0.1, poll_seconds=0.005):
                self.assertTrue(lock.exists())
            self.assertFalse(lock.exists())
            lock.write_text(json.dumps({
                "pid": 99999999, "host": "another-host.invalid",
                "acquired_at": time.time() - 601,
            }))
            with self.assertRaises(LibraryLockError):
                with LibraryLock(root, timeout_seconds=0.02, poll_seconds=0.005):
                    pass

            for malformed in ([], {"pid": 99999999, "host": socket.gethostname(), "acquired_at": float("nan")}):
                lock.write_text(json.dumps(malformed))
                with self.assertRaises(LibraryLockError):
                    with LibraryLock(root, timeout_seconds=0.02, poll_seconds=0.005):
                        pass

    def test_concurrent_registration_preserves_both_index_entries(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            library = base / "library"
            packages = []
            for panel_id in ("panel-a", "panel-b"):
                brief = copy.deepcopy(self.brief)
                panel = copy.deepcopy(self.panel)
                panel.update(panel_id=panel_id, panel_name=panel_id)
                packages.append(build_audience_package(brief, panel, base / panel_id).package_zip_path)
            env = {**os.environ, "AUDIENCE_LAB_LIBRARY_DIR": str(library)}
            processes = [subprocess.Popen(
                [sys.executable, str(CLI), "register", str(path)], stdout=subprocess.PIPE,
                stderr=subprocess.PIPE, text=True, env=env,
            ) for path in packages]
            results = [p.communicate(timeout=15) + (p.returncode,) for p in processes]
            self.assertEqual([r[2] for r in results], [0, 0], results)
            self.assertEqual({x["panel_id"] for x in list_panels(library_root=library)["panels"]}, {"panel-a", "panel-b"})

    def test_cli_json_and_exit_codes(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            package = self._package(base / "package")
            env = {**os.environ, "AUDIENCE_LAB_LIBRARY_DIR": str(base / "library")}
            ok = subprocess.run([sys.executable, str(CLI), "register", str(package.package_zip_path)], capture_output=True, text=True, env=env)
            self.assertEqual(ok.returncode, 0, ok.stderr)
            self.assertEqual(json.loads(ok.stdout)["status"], "registered")
            missing = subprocess.run([sys.executable, str(CLI), "show", "missing", "1.0.0"], capture_output=True, text=True, env=env)
            self.assertEqual(missing.returncode, 4)
            self.assertEqual(json.loads(missing.stdout)["error"], "not_found")
            unsafe = subprocess.run([sys.executable, str(CLI), "show", "../bad", "1.0.0"], capture_output=True, text=True, env=env)
            self.assertEqual(unsafe.returncode, 6)
            json.loads(unsafe.stdout)


if __name__ == "__main__":
    unittest.main()
