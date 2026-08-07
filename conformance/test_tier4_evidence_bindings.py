from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "skills" / "audience-panel-builder" / "scripts"))

from audience_panel_builder.common import sha256_json  # noqa: E402
from audience_panel_builder.population.validation.evidence_bindings import (  # noqa: E402
    LINEAGE_ORDER,
    bind_json,
    bind_jsonl,
    lineage_bundle_sha256,
)
from audience_panel_builder.population.validation.evidence_errors import (  # noqa: E402
    ProducerAuthenticationError,
)


def digest(value: bytes) -> str:
    return "sha256:" + hashlib.sha256(value).hexdigest()


class Tier4EvidenceBindingsTests(unittest.TestCase):
    def write(self, root: Path, name: str, value: bytes) -> Path:
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(value)
        return path

    def test_json_binds_distinct_raw_and_canonical_non_ascii_bytes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            payload = {"z": [3, 2], "creative_id": "créative-é", "meta": {"a": True}}
            raw = json.dumps(payload, indent=2, ensure_ascii=True).encode("utf-8") + b"\n"
            path = self.write(root, "result.json", raw)

            binding = bind_json(path, root=root)

            canonical = json.dumps(
                payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False,
            ).encode("utf-8") + b"\n"
            self.assertEqual(
                {
                    "path": "result.json",
                    "raw_bytes_sha256": digest(raw),
                    "canonical_document_sha256": digest(canonical),
                    "record_count": None,
                },
                binding,
            )
            self.assertNotEqual(binding["raw_bytes_sha256"], binding["canonical_document_sha256"])

            whitespace = self.write(root, "whitespace.json", b" \n" + raw + b" \t\n")
            changed = bind_json(whitespace, root=root)
            self.assertNotEqual(binding["raw_bytes_sha256"], changed["raw_bytes_sha256"])
            self.assertEqual(binding["canonical_document_sha256"], changed["canonical_document_sha256"])

    def test_jsonl_preserves_physical_record_order_and_canonical_lfs(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            records = [{"creative_id": "créative-é", "score": 2}, {"creative_id": "creative-a", "score": 1}]
            raw = (
                b' { "creative_id": "cr\\u00e9ative-\\u00e9", "score": 2 }\n'
                b'{"score":1,"creative_id":"creative-a"}\n'
            )
            path = self.write(root, "lineage/accepted.jsonl", raw)

            binding = bind_jsonl(path, root=root)

            canonical = b"".join(
                json.dumps(record, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8") + b"\n"
                for record in records
            )
            self.assertEqual("lineage/accepted.jsonl", binding["path"])
            self.assertEqual(2, binding["record_count"])
            self.assertEqual(digest(raw), binding["raw_bytes_sha256"])
            self.assertEqual(digest(canonical), binding["canonical_document_sha256"])

            reordered = self.write(root, "lineage/reordered.jsonl", raw.splitlines()[1] + b"\n" + raw.splitlines()[0] + b"\n")
            self.assertNotEqual(
                binding["canonical_document_sha256"],
                bind_jsonl(reordered, root=root)["canonical_document_sha256"],
            )

    def test_lineage_digest_requires_closed_fixed_ordered_unique_bindings(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            bindings = {}
            for name in LINEAGE_ORDER:
                bindings[name] = bind_jsonl(self.write(root, f"{name}.jsonl", b'{"id":"x"}\n'), root=root)

            self.assertEqual(
                sha256_json([bindings[name] for name in LINEAGE_ORDER]),
                lineage_bundle_sha256(bindings),
            )
            reordered = dict(reversed(list(bindings.items())))
            with self.assertRaises(ProducerAuthenticationError):
                lineage_bundle_sha256(reordered)
            duplicate = dict(bindings)
            duplicate["dispatch_audit"] = dict(duplicate["accepted_responses"])
            with self.assertRaises(ProducerAuthenticationError):
                lineage_bundle_sha256(duplicate)
            unknown = dict(bindings)
            unknown["accepted_responses"] = {**unknown["accepted_responses"], "unexpected": True}
            with self.assertRaises(ProducerAuthenticationError):
                lineage_bundle_sha256(unknown)

    def test_rejects_unsafe_or_noncanonical_inputs(self):
        with tempfile.TemporaryDirectory() as directory, tempfile.TemporaryDirectory() as other_directory:
            root = Path(directory)
            outside = self.write(Path(other_directory), "outside.json", b"{}")
            for name, content, binder in (
                ("malformed.json", b"{", bind_json),
                ("nan.json", b'{"score":NaN}', bind_json),
                ("empty.jsonl", b"", bind_jsonl),
                ("partial.jsonl", b'{"id":"x"}', bind_jsonl),
                ("blank.jsonl", b'{"id":"x"}\n\n', bind_jsonl),
                ("nan.jsonl", b'{"score":NaN}\n', bind_jsonl),
                ("duplicate-root.json", b'{"id":"first","id":"second"}', bind_json),
                ("duplicate-nested.json", b'{"outer":{"id":"first","id":"second"}}', bind_json),
                ("duplicate-root.jsonl", b'{"id":"first","id":"second"}\n', bind_jsonl),
                ("duplicate-nested.jsonl", b'{"outer":{"id":"first","id":"second"}}\n', bind_jsonl),
            ):
                with self.subTest(name=name), self.assertRaises(ProducerAuthenticationError):
                    binder(self.write(root, name, content), root=root)
            with self.assertRaises(ProducerAuthenticationError):
                bind_json(outside, root=root)
            self.write(root, "target.json", b"{}")
            (root / "symlink.json").symlink_to(root / "target.json")
            with self.assertRaises(ProducerAuthenticationError):
                bind_json(root / "symlink.json", root=root)
            with self.assertRaises(ProducerAuthenticationError):
                bind_json(root / ".." / Path(other_directory).name / "outside.json", root=root)

    def test_rejects_source_mutated_while_descriptor_is_read(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = self.write(root, "result.json", b'{"creative_id":"creative-a"}')
            real_read = os.read
            calls = 0

            def mutate(fd: int, size: int) -> bytes:
                nonlocal calls
                calls += 1
                value = real_read(fd, size)
                if calls == 1:
                    path.write_bytes(b'{"creative_id":"creative-b"}')
                return value

            with patch(
                "audience_panel_builder.population.validation.evidence_bindings.os.read",
                side_effect=mutate,
            ), self.assertRaises(ProducerAuthenticationError):
                bind_json(path, root=root)


if __name__ == "__main__":
    unittest.main()
