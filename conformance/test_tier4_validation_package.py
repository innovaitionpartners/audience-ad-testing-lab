from __future__ import annotations

from copy import deepcopy
import hashlib
import io
import json
from pathlib import Path
import sys
import tempfile
import threading
import unittest
import zipfile


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "skills" / "audience-panel-builder" / "scripts"))

from audience_panel_builder.common import sha256_json  # noqa: E402
from audience_panel_builder.population.validation.package import (  # noqa: E402
    ARCHIVE_FILES, CLAIM_MEMBER, ValidationPackageError,
    build_validation_package as _build_validation_package,
    validate_validation_package as _validate_validation_package,
)
from conformance.test_tier4_held_out_evaluation import (  # noqa: E402
    _AUTHORITY_REGISTRIES, build_claim_family, comparison,
    evaluate_held_out_ordering, issue_tier4_claim, seal_preregistration,
    sealed_registration,
)
from conformance.test_tier4_validation_contracts import (  # noqa: E402
    observation_fixture, shared_outcome_evidence_fixture,
)


def canonical(value: object) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n").encode()


def build_validation_package(**kwargs: object) -> Path:
    return _build_validation_package(
        **kwargs, authority_registry=_AUTHORITY_REGISTRIES,
    )


def validate_validation_package(path: Path) -> dict[str, object]:
    return _validate_validation_package(
        path, authority_registry=_AUTHORITY_REGISTRIES,
    )


class Tier4ValidationPackageTests(unittest.TestCase):
    def _panel(self, root: Path) -> Path:
        from conformance.test_audience_package_v3 import AudiencePackageV3Test
        harness = AudiencePackageV3Test(); harness.setUp()
        return harness._build(root).package_zip_path

    def _panel_v2(self, root: Path) -> Path:
        from conformance.test_audience_package import AudiencePackageTest
        harness = AudiencePackageTest(); harness.setUp()
        return harness._build(root).package_zip_path

    def _inputs(
        self, root: Path, panel: Path, *, negative: bool = False,
        registration_id: str = "validation-q3",
    ) -> dict[str, Path]:
        # Bind the fully supported held-out fixture to the authenticated base
        # package, then add the aggregate-only observation evidence needed by
        # the closed archive world.
        from audience_lab.audience_package import ARCHIVE_FILES, PACKAGE_SCHEMA_VERSION, read_safe_archive_members
        from audience_lab.audience_package_dispatch import validate_supported_audience_package
        from audience_lab.audience_package_v3 import archive_files_v3_for_manifest, read_v3_archive_manifest, read_v3_archive_members
        raw, manifest_bytes = read_v3_archive_manifest(panel)
        manifest = json.loads(manifest_bytes)
        members = read_safe_archive_members(raw, allowed_files=ARCHIVE_FILES) if manifest.get("schema_version") == PACKAGE_SCHEMA_VERSION else read_v3_archive_members(raw, allowed_files=archive_files_v3_for_manifest(manifest))
        valid = validate_supported_audience_package(panel)
        binding = {"panel_id": valid["panel_id"], "panel_version": valid["panel_version"], "panel_sha256": "sha256:" + hashlib.sha256(members["saved-audience-panel.json"]).hexdigest(), "package_sha256": "sha256:" + valid["package_zip_sha256"]}
        registration = sealed_registration(registration_id=registration_id); registration["panel_binding"] = binding; registration["claim_scope"]["panel_binding"] = binding; registration["registration_sha256"] = None
        registration = seal_preregistration(registration)
        comparisons = [comparison(registration, index, reverse=negative) for index in range(12)]
        family = build_claim_family(registrations=[registration], comparisons_by_registration={registration["registration_id"]: comparisons}, built_at="2026-09-01T00:00:00Z")
        evaluation = evaluate_held_out_ordering(registration=registration, comparisons=comparisons, claim_family=family, evaluated_at="2026-09-01T00:00:00Z")
        claim = None if negative else issue_tier4_claim(evaluation=evaluation, issued_at="2026-09-01T01:00:00Z", expires_at="2027-03-01T00:00:00Z")
        negative_result: dict[str, object] | None = None
        if negative:
            negative_result = {
                "schema_version": "panel-tier4-negative-result-v1",
                "evaluation_binding": {"evaluation_id": evaluation["evaluation_id"], "evaluation_sha256": evaluation["evaluation_sha256"]},
                "panel_binding": binding, "claim_scope": registration["claim_scope"],
                "status": "tier4_not_supported", "limitations": ["not supported"],
                "negative_result_sha256": None,
            }
            negative_result["negative_result_sha256"] = sha256_json({**negative_result, "negative_result_sha256": None})
        observations = sorted(
            [
                deepcopy(observation)
                for item in comparisons
                for observation in item["observations"]
            ],
            key=lambda item: str(item["observation_id"]),
        )
        from audience_panel_builder.population.validation.contracts import (
            project_shared_outcome_evidence,
        )
        shared = project_shared_outcome_evidence(observations[0])
        from audience_panel_builder.population.validation.reporting import (
            build_validation_report_payload,
            render_validation_report_bytes,
        )
        from audience_panel_builder.population.validation.package import (
            CANONICAL_REPORT_TEMPLATE,
        )
        report = render_validation_report_bytes(
            payload=build_validation_report_payload(
                registration=registration,
                evaluation=evaluation,
                claim=claim,
                as_of=evaluation["evaluated_at"],
                authority_registry=_AUTHORITY_REGISTRIES,
            ),
            template_path=CANONICAL_REPORT_TEMPLATE,
        )
        report_manifest = {
            "schema_version": "panel-validation-report-manifest-v1",
            "panel_binding": binding,
            "evaluation_sha256": evaluation["evaluation_sha256"],
            "result_sha256": negative_result["negative_result_sha256"] if negative_result else claim["claim_sha256"],
            "report_sha256": hashlib.sha256(report).hexdigest(),
            "report_byte_count": len(report),
        }
        docs: dict[str, object] = {
            "panel-validation-preregistration.json": registration,
            "panel-shared-outcome-evidence.json": shared,
            "panel-validation-observations.json": observations,
            "panel-synthetic-outcome-comparisons.json": comparisons,
            "panel-held-out-evaluation.json": evaluation,
            "panel-validation-claim-family.json": family,
            "source-inventory.json": {
                "schema_version": "panel-validation-source-inventory-v1",
                "aggregate_only": True,
                "sources": [deepcopy(shared["source"])],
            },
            "panel-validation-report-manifest.json": report_manifest,
        }
        docs["panel-tier4-negative-result.json" if negative_result else "panel-tier4-claim.json"] = negative_result or claim
        root.mkdir(parents=True, exist_ok=True)
        paths: dict[str, Path] = {}
        for name, value in docs.items():
            path = root / name; path.write_bytes(canonical(value)); paths[name] = path
        report_path = root / "panel-validation-report.html"; report_path.write_bytes(report); paths[report_path.name] = report_path
        return paths

    def test_package_is_deterministic_and_binds_base_panel_package(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); panel = self._panel(root / "base")
            first = build_validation_package(inputs=self._inputs(root / "one", panel), panel_package_path=panel, output_dir=root / "out-one")
            second = build_validation_package(inputs=self._inputs(root / "two", panel), panel_package_path=panel, output_dir=root / "out-two")
            self.assertEqual(first.read_bytes(), second.read_bytes())
            validated = validate_validation_package(first)
            self.assertEqual("audience-panel-validation-package-v1", validated["schema_version"])
            self.assertEqual("sha256:" + hashlib.sha256(panel.read_bytes()).hexdigest(), validated["panel_binding"]["package_sha256"])
            with zipfile.ZipFile(first) as archive:
                self.assertEqual(list(ARCHIVE_FILES), archive.namelist())
            negative = build_validation_package(inputs=self._inputs(root / "negative", panel, negative=True), panel_package_path=panel, output_dir=root / "out-negative")
            with zipfile.ZipFile(negative) as archive:
                self.assertEqual(["panel-tier4-negative-result.json" if name == CLAIM_MEMBER else name for name in ARCHIVE_FILES], archive.namelist())

    def test_pii_tamper_and_no_clobber_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); panel = self._panel(root / "base"); inputs = self._inputs(root / "inputs", panel)
            source = json.loads(inputs["source-inventory.json"].read_text()); source["email"] = "person@example.com"; inputs["source-inventory.json"].write_bytes(canonical(source))
            with self.assertRaisesRegex(ValidationPackageError, "person-level|PII"):
                build_validation_package(inputs=inputs, panel_package_path=panel, output_dir=root / "out")
            inputs = self._inputs(root / "clean", panel)
            archive = build_validation_package(inputs=inputs, panel_package_path=panel, output_dir=root / "out")
            with self.assertRaises(ValidationPackageError):
                build_validation_package(inputs=inputs, panel_package_path=panel, output_dir=root / "out")
            raw = archive.read_bytes(); changed = bytearray(raw); changed[-1] ^= 1
            bad = root / "bad.zip"; bad.write_bytes(changed)
            with self.assertRaises(Exception): validate_validation_package(bad)

    def test_v2_panel_package_and_closed_aggregate_source_inventory_are_supported(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); panel = self._panel_v2(root / "base-v2")
            inputs = self._inputs(root / "inputs-v2", panel)
            package = build_validation_package(inputs=inputs, panel_package_path=panel, output_dir=root / "out-v2")
            self.assertEqual("valid", validate_validation_package(package)["status"])
            raw_rows = json.loads((root / "inputs-v2" / "source-inventory.json").read_text())
            raw_rows["sources"][0]["person_id"] = "person-1"
            (root / "inputs-v2" / "source-inventory.json").write_bytes(canonical(raw_rows))
            with self.assertRaisesRegex(ValidationPackageError, "allowlist|person-level|PII"):
                build_validation_package(inputs=inputs, panel_package_path=panel, output_dir=root / "bad-v2")

    def test_member_publication_never_overwrites_a_precreated_or_concurrent_final(self) -> None:
        from audience_panel_builder.population.validation.package import _atomic_write_new
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); final = root / "member.json"; final.write_bytes(b"owner")
            with self.assertRaises(ValidationPackageError): _atomic_write_new(final, b"replacement")
            self.assertEqual(b"owner", final.read_bytes())
            target = root / "race.json"; barrier = threading.Barrier(2); outcomes: list[str] = []
            def publish(payload: bytes) -> None:
                barrier.wait()
                try: _atomic_write_new(target, payload); outcomes.append("published")
                except ValidationPackageError: outcomes.append("collision")
            first = threading.Thread(target=publish, args=(b"first",)); second = threading.Thread(target=publish, args=(b"second",)); first.start(); second.start(); first.join(); second.join()
            self.assertEqual(["collision", "published"], sorted(outcomes))
            self.assertIn(target.read_bytes(), {b"first", b"second"})
