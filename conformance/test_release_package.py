from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
import zipfile


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / ".github" / "scripts" / "build_release_package.py"
SPEC = importlib.util.spec_from_file_location("build_release_package", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class ReleasePackageTests(unittest.TestCase):
    def test_archive_is_deterministic_complete_and_uploadable(self) -> None:
        with tempfile.TemporaryDirectory() as first, tempfile.TemporaryDirectory() as second:
            archive_one = MODULE.build_archive(ROOT, Path(first))
            archive_two = MODULE.build_archive(ROOT, Path(second))
            self.assertEqual(archive_one.name, "audience-ad-testing-lab-plugin-1.0.2.zip")
            self.assertLess(archive_one.stat().st_size, MODULE.MAX_CLAUDE_UPLOAD_BYTES)
            self.assertEqual(
                hashlib.sha256(archive_one.read_bytes()).digest(),
                hashlib.sha256(archive_two.read_bytes()).digest(),
            )

            manifest = json.loads((ROOT / MODULE.MANIFEST_PATH).read_text(encoding="utf-8"))
            expected = sorted({*manifest["files"], "README.md"})
            with zipfile.ZipFile(archive_one) as archive:
                self.assertEqual(expected, archive.namelist())
                self.assertIsNone(archive.testzip())
                self.assertIn(".claude-plugin/plugin.json", archive.namelist())
                self.assertIn(".codex-plugin/plugin.json", archive.namelist())


if __name__ == "__main__":
    unittest.main()
