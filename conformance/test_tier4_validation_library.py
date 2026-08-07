from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "skills" / "audience-panel-builder" / "scripts"))

from audience_panel_builder.population.validation.library import (  # noqa: E402
    ImmutableVersionConflict,
    append_claim_lifecycle_event as _append_claim_lifecycle_event,
    claim_lifecycle_status as _claim_lifecycle_status,
    current_claim as _current_claim,
    list_claims as _list_claims,
    register_validation_package as _register_validation_package,
    show_claim as _show_claim,
)
from audience_panel_builder.common import canonical_json_bytes, sha256_json  # noqa: E402
from conformance.test_tier4_held_out_evaluation import (  # noqa: E402
    _AUTHORITY_REGISTRIES,
)
from conformance.test_tier4_validation_package import (  # noqa: E402
    build_validation_package,
)


def register_validation_package(*args: object, **kwargs: object) -> dict[str, object]:
    return _register_validation_package(
        *args, **kwargs, authority_registry=_AUTHORITY_REGISTRIES,
    )


def list_claims(**kwargs: object) -> dict[str, object]:
    return _list_claims(
        **kwargs, authority_registry=_AUTHORITY_REGISTRIES,
    )


def show_claim(*args: object, **kwargs: object) -> dict[str, object]:
    return _show_claim(
        *args, **kwargs, authority_registry=_AUTHORITY_REGISTRIES,
    )


def current_claim(*args: object, **kwargs: object) -> dict[str, object]:
    return _current_claim(
        *args, **kwargs, authority_registry=_AUTHORITY_REGISTRIES,
    )


def append_claim_lifecycle_event(**kwargs: object) -> dict[str, object]:
    return _append_claim_lifecycle_event(
        **kwargs, authority_registry=_AUTHORITY_REGISTRIES,
    )


def claim_lifecycle_status(
    *args: object, **kwargs: object,
) -> dict[str, object]:
    return _claim_lifecycle_status(
        *args, **kwargs, authority_registry=_AUTHORITY_REGISTRIES,
    )


class Tier4ValidationLibraryTests(unittest.TestCase):
    def test_registration_publishes_the_exact_validated_snapshot_and_never_clobbers(self) -> None:
        from audience_panel_builder.population.validation import library as lib
        from conformance.test_tier4_validation_package import Tier4ValidationPackageTests
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            helper = Tier4ValidationPackageTests()
            panel = helper._panel(root / "panel")
            source = build_validation_package(
                inputs=helper._inputs(root / "inputs", panel),
                panel_package_path=panel,
                output_dir=root / "out",
            )
            validated_bytes = source.read_bytes()
            real_validate = lib.validate_validation_package

            def mutate_source_after_validation(
                path: Path, *, authority_registry: object,
            ):
                result = real_validate(
                    path, authority_registry=authority_registry,
                )
                source.write_bytes(b"changed-after-validation")
                return result

            library = root / "library"
            with patch.object(
                lib,
                "validate_validation_package",
                side_effect=mutate_source_after_validation,
            ):
                result = register_validation_package(
                    source,
                    library_root=library,
                    registered_at="2026-09-02T00:00:00Z",
                )
            target = (
                library / result["claim"]["relative_path"]
            )
            self.assertEqual(validated_bytes, target.read_bytes())
            self.assertEqual(
                result["claim"]["claim_id"],
                show_claim(
                    result["claim"]["claim_id"], library_root=library,
                )["claim"]["claim_id"],
            )

            source.write_bytes(validated_bytes)
            racing_library = root / "racing-library"
            target = (
                racing_library / "claims" / result["claim"]["claim_id"]
                / "audience-panel-validation-package.zip"
            )
            real_link = lib.os.link

            def precreate_target(source_path, destination_path, **kwargs):
                destination = Path(destination_path)
                if not destination.exists():
                    destination.write_bytes(b"other-owner")
                return real_link(source_path, destination_path, **kwargs)

            with patch.object(lib.os, "link", side_effect=precreate_target):
                with self.assertRaises(ImmutableVersionConflict):
                    register_validation_package(
                        source,
                        library_root=racing_library,
                        registered_at="2026-09-02T00:00:00Z",
                    )
            self.assertEqual(b"other-owner", target.read_bytes())

    def test_registry_is_immutable_and_lifecycle_current_lookup_is_exact(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            from conformance.test_tier4_validation_package import Tier4ValidationPackageTests
            root = Path(temporary); helper = Tier4ValidationPackageTests()
            # Rebuild exactly one package from the helper's authenticated inputs.
            panel = helper._panel(root / "panel"); inputs = helper._inputs(root / "inputs-real", panel)
            package = build_validation_package(inputs=inputs, panel_package_path=panel, output_dir=root / "out")
            library = root / "library"
            first = register_validation_package(package, library_root=library, registered_at="2026-09-02T00:00:00Z")["claim"]
            self.assertEqual("already_registered", register_validation_package(package, library_root=library, registered_at="2026-09-02T00:00:00Z")["status"])
            self.assertEqual(1, len(list_claims(library_root=library)["claims"]))
            with self.assertRaises(Exception):
                altered = root / "altered.zip"; altered.write_bytes(package.read_bytes() + b"x")
                register_validation_package(altered, library_root=library, registered_at="2026-09-02T00:00:00Z")
            event = append_claim_lifecycle_event(claim_id=first["claim_id"], event_type="withdrawn", effective_at="2026-10-01T00:00:00Z", actor_id="maintainer-001", reason="Evidence was withdrawn.", evidence_sha256=[first["claim_sha256"]], replacement_claim_id=None, library_root=library)
            self.assertEqual(first["claim_id"], event["claim_id"])
            shown = show_claim(first["claim_id"], library_root=library)
            self.assertEqual(1, shown["claim"]["event_count"])
            self.assertEqual(event["event_sha256"], shown["claim"]["event_head_sha256"])
            with self.assertRaises(Exception):
                current_claim(first["panel_id"], first["panel_version"], first["claim_scope_sha256"], library_root=library, as_of="2026-10-02T00:00:00Z")

    def test_event_log_and_index_identity_are_commitments(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            from conformance.test_tier4_validation_package import Tier4ValidationPackageTests
            root = Path(temporary); helper = Tier4ValidationPackageTests(); panel = helper._panel(root / "panel")
            package = build_validation_package(inputs=helper._inputs(root / "inputs", panel), panel_package_path=panel, output_dir=root / "out")
            library = root / "library"; claim = register_validation_package(package, library_root=library, registered_at="2026-09-02T00:00:00Z")["claim"]
            append_claim_lifecycle_event(claim_id=claim["claim_id"], event_type="invalidated", effective_at="2026-10-01T00:00:00Z", actor_id="maintainer-001", reason="Invalidated.", evidence_sha256=[claim["claim_sha256"]], replacement_claim_id=None, library_root=library)
            events = library / "claims" / claim["claim_id"] / "events.jsonl"; events.write_bytes(b"")
            with self.assertRaises(Exception): show_claim(claim["claim_id"], library_root=library)
            # A fresh registry proves that cached identity/expiry fields cannot
            # silently authorize a package after index tampering.
            library2 = root / "library-two"; claim2 = register_validation_package(package, library_root=library2, registered_at="2026-09-02T00:00:00Z")["claim"]
            index_path = library2 / "index.json"; index = json.loads(index_path.read_text()); index["claims"][0]["expires_at"] = "2030-01-01T00:00:00Z"; index_path.write_text(json.dumps(index))
            with self.assertRaises(Exception): current_claim(claim2["panel_id"], claim2["panel_version"], claim2["claim_scope_sha256"], library_root=library2, as_of="2027-01-01T00:00:00Z")

    def test_supersession_and_explicit_expiry_are_append_only_lifecycle_paths(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            from conformance.test_tier4_validation_package import Tier4ValidationPackageTests
            root = Path(temporary); helper = Tier4ValidationPackageTests(); panel = helper._panel(root / "panel")
            first_package = build_validation_package(inputs=helper._inputs(root / "first-inputs", panel), panel_package_path=panel, output_dir=root / "first-output")
            replacement_inputs = helper._inputs(
                root / "replacement-inputs",
                panel,
                registration_id="validation-q3-replacement",
            )
            second_package = build_validation_package(inputs=replacement_inputs, panel_package_path=panel, output_dir=root / "replacement-output")
            library = root / "library"; first = register_validation_package(first_package, library_root=library, registered_at="2026-09-02T00:00:00Z")["claim"]; second = register_validation_package(second_package, library_root=library, registered_at="2026-09-03T00:00:00Z")["claim"]
            append_claim_lifecycle_event(claim_id=first["claim_id"], event_type="superseded", effective_at="2026-10-01T00:00:00Z", actor_id="maintainer-001", reason="Replacement is independently validated.", evidence_sha256=[second["claim_sha256"]], replacement_claim_id=second["claim_id"], library_root=library)
            self.assertEqual(second["claim_id"], current_claim(first["panel_id"], first["panel_version"], first["claim_scope_sha256"], library_root=library, as_of="2026-10-02T00:00:00Z")["claim"]["claim_id"])
            expiry_library = root / "expiry-library"; expiry = register_validation_package(first_package, library_root=expiry_library, registered_at="2026-09-02T00:00:00Z")["claim"]
            event = append_claim_lifecycle_event(claim_id=expiry["claim_id"], event_type="expired", effective_at=expiry["expires_at"], actor_id="maintainer-001", reason="Claim reached its declared expiry.", evidence_sha256=[expiry["claim_sha256"]], replacement_claim_id=None, library_root=expiry_library)
            self.assertEqual("expired", event["event_type"])

    def test_pending_lifecycle_transactions_recover_each_durable_boundary(self) -> None:
        from audience_panel_builder.population.validation import library as lib
        from conformance.test_tier4_validation_package import Tier4ValidationPackageTests
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); helper = Tier4ValidationPackageTests(); panel = helper._panel(root / "panel")
            package = build_validation_package(inputs=helper._inputs(root / "inputs", panel), panel_package_path=panel, output_dir=root / "out")
            for phase in ("after-wal", "after-event", "after-index"):
                with self.subTest(phase=phase):
                    library = root / phase; claim = register_validation_package(package, library_root=library, registered_at="2026-09-02T00:00:00Z")["claim"]
                    event = {"schema_version": lib.CLAIM_LIFECYCLE_EVENT_VERSION, "claim_id": claim["claim_id"], "event_type": "withdrawn", "effective_at": "2026-10-01T00:00:00Z", "actor_id": "maintainer-001", "reason": "Recovery test.", "evidence_sha256": [claim["claim_sha256"]], "replacement_claim_id": None, "previous_event_sha256": None, "event_sha256": None}
                    event["event_sha256"] = sha256_json(event)
                    transaction = lib._transaction(event, claim)
                    lib._atomic(lib._transaction_path(library), canonical_json_bytes(transaction))
                    event_path = lib._events_path(library, claim["claim_id"])
                    if phase in {"after-event", "after-index"}:
                        lib._append_event_bytes(event_path, event)
                    if phase == "after-index":
                        lib._write_lifecycle_index(library, lib._read_index(library), claim_id=claim["claim_id"], count=1, head=event["event_sha256"], updated_at=event["effective_at"])
                    self.assertEqual(1, show_claim(claim["claim_id"], library_root=library)["claim"]["event_count"])
                    self.assertFalse(lib._transaction_path(library).exists())
            bad_library = root / "tampered"; claim = register_validation_package(package, library_root=bad_library, registered_at="2026-09-02T00:00:00Z")["claim"]
            lib._atomic(lib._transaction_path(bad_library), b'{"tampered":true}\n')
            with self.assertRaises(Exception): show_claim(claim["claim_id"], library_root=bad_library)

    def test_pending_lifecycle_transaction_recovers_a_partial_event_append(self) -> None:
        from audience_panel_builder.population.validation import library as lib
        from conformance.test_tier4_validation_package import Tier4ValidationPackageTests
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            helper = Tier4ValidationPackageTests()
            panel = helper._panel(root / "panel")
            package = build_validation_package(
                inputs=helper._inputs(root / "inputs", panel),
                panel_package_path=panel,
                output_dir=root / "out",
            )
            library = root / "library"
            claim = register_validation_package(
                package,
                library_root=library,
                registered_at="2026-09-02T00:00:00Z",
            )["claim"]
            event = {
                "schema_version": lib.CLAIM_LIFECYCLE_EVENT_VERSION,
                "claim_id": claim["claim_id"],
                "event_type": "withdrawn",
                "effective_at": "2026-10-01T00:00:00Z",
                "actor_id": "maintainer-001",
                "reason": "Partial append recovery test.",
                "evidence_sha256": [claim["claim_sha256"]],
                "replacement_claim_id": None,
                "previous_event_sha256": None,
                "event_sha256": None,
            }
            event["event_sha256"] = sha256_json(event)
            transaction = lib._transaction(event, claim)
            lib._atomic(
                lib._transaction_path(library),
                canonical_json_bytes(transaction),
            )
            event_path = lib._events_path(library, claim["claim_id"])
            event_path.parent.mkdir(parents=True, exist_ok=True)
            event_bytes = canonical_json_bytes(event)
            event_path.write_bytes(event_bytes[: len(event_bytes) // 2])

            recovered = show_claim(claim["claim_id"], library_root=library)
            self.assertEqual(1, recovered["claim"]["event_count"])
            self.assertEqual(event, recovered["events"][0])
            self.assertFalse(lib._transaction_path(library).exists())
