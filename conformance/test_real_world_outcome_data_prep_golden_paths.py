from __future__ import annotations

from contextlib import contextmanager
from contextlib import redirect_stderr
from copy import deepcopy
import csv
import hashlib
import importlib.util
import io
import json
from pathlib import Path
from types import SimpleNamespace
import sys
import tempfile
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
PREP_SCRIPTS = ROOT / "skills" / "real-world-outcome-data-prep" / "scripts"
PANEL_SCRIPTS = ROOT / "skills" / "audience-panel-builder" / "scripts"
sys.path.insert(0, str(PREP_SCRIPTS))
sys.path.insert(0, str(PANEL_SCRIPTS))

from audience_panel_builder.population.validation import contracts as tier4_contracts  # noqa: E402
from conformance.test_tier4_validation_contracts import (  # noqa: E402
    AUTHORITY_SECRET,
    AUTHORITY_SECRET_SHA256,
    authority_registry_document,
    preregistration_fixture,
)
from audience_panel_builder.population.validation.evidence_bindings import (  # noqa: E402
    LINEAGE_ORDER,
)
from outcome_data_prep.common import (  # noqa: E402
    ContractError,
    canonical_json_bytes,
    sha256_bytes,
    sha256_json,
)
from outcome_data_prep.registration import (  # noqa: E402
    RegistrationDraft,
    build_registration_draft,
    seal_study_registration,
)
from outcome_data_prep.publication import (  # noqa: E402
    ImportConflict,
    commit_import_generation,
    replay_authenticated_ledger,
    validate_complete_staged_generation,
)
from outcome_data_prep.adapters.base import AdapterValidation  # noqa: E402
from outcome_data_prep.container_safety import inspect_container  # noqa: E402
from outcome_data_prep.privacy import container_inventory_sha256  # noqa: E402
from outcome_data_prep.source_snapshot import snapshot_source  # noqa: E402
from outcome_data_prep.contracts import (  # noqa: E402
    READINESS_VERSION,
    SOURCE_GOVERNANCE_INPUT_VERSION,
    validate_readiness_report,
    validate_source_governance_input,
)
from outcome_data_prep.reporting import (  # noqa: E402
    READINESS_HEADINGS,
    render_readiness_report,
)
from outcome_data_prep.runtime_guard import (  # noqa: E402
    RuntimeGuardError,
    require_approved_runtime,
    verify_runtime_identity,
)
from outcome_data_prep.study_authority import (  # noqa: E402
    IMPORT_EVENT_DOMAIN,
    StudyAuthority,
    StudyAuthorityError,
    authenticate_import_event,
    authenticate_study_receipt,
    authority_hmac,
    import_event_authority_projection,
)
from outcome_data_prep.workflow import (  # noqa: E402
    CorrectionInput,
    ImportRequest,
    ImportSafetyError,
    SourceInput,
    import_results,
    pair_source_arguments,
    recover_study_from_paths,
    validate_study,
)
from outcome_data_prep.workflow import _correction_static_projection  # noqa: E402
from outcome_data_prep.validation_handoff import (  # noqa: E402
    validate_validation_handoff_document,
)


RELEASE_MANIFEST = (
    ROOT
    / "skills"
    / "real-world-outcome-data-prep"
    / "references"
    / "runtime-release-manifest.json"
)


class OutcomePrepStudyGoldenPaths(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.run_root = self.root / "run"
        self.run_root.mkdir()
        self.panel_package = self.root / "panel.zip"
        self.panel_package.write_bytes(b"authenticated-by-patched-panel-boundary")
        self.output = self.root / "sealed-study"
        self.authority_root = self.root / "authority-root.json"
        self.authority_index = self.root / "authority-index.json"
        self.authority_root.write_bytes(b"authority root\n")
        self.authority_index.write_bytes(b"authority index\n")
        self.authority_secret_file = self.root / "owner-only.key"
        self.authority_secret_file.write_bytes(AUTHORITY_SECRET)
        self.authority_secret_file.chmod(0o600)
        self._write_run()
        self.producer_evidence_root = self.root / "producer-evidence"
        self.producer_snapshot_root = self.root / "producer-snapshots"
        self.producer_evidence_root.mkdir()
        self.producer_snapshot_root.mkdir()
        self.authenticated_snapshot_root = self.root / "authenticated-snapshot"
        self.authenticated_snapshot_root.mkdir()
        for name in ("study-manifest.json", "screening-model-results.json"):
            (self.authenticated_snapshot_root / name).write_bytes(
                (self.run_root / name).read_bytes()
            )
        self.producer_receipt = self._producer_receipt()

        template = preregistration_fixture()
        self.synthetic_result_sha256 = sha256_json(
            json.loads(
                (self.run_root / "screening-model-results.json").read_text(
                    encoding="utf-8"
                )
            )
        )
        template["registered_at"] = "2026-07-31T10:00:00Z"
        template["approval"]["approved_at"] = "2026-07-31T09:59:00Z"
        template["registration_sha256"] = None
        self.supplied_facts = {
            "preregistration_template": template,
            "primary_metric": template["primary_metric"],
            "metric_direction": "higher_is_better",
            "measurement_window": "2026-q3",
            "attribution_window": "seven-days",
            "validation_blocks": template["validation_blocks"],
            "minimum_effect": 0.03,
            "missing_data_rule": "report",
            "permission_reference": "permission-one",
            "registered_by": "validation-owner",
            "approved_by": "validation-owner",
            "registered_at": "2026-07-31T10:00:00Z",
            "approved_at": "2026-07-31T09:59:00Z",
            "delivery_map_sealed_at": "2026-07-31T10:00:00Z",
            "producer_evidence_root": self.producer_evidence_root,
            "producer_snapshot_root": self.producer_snapshot_root,
            "delivery_start_evidence": {
                "occurred_at": "2026-07-31T11:00:00Z",
                "evidence_source_sha256": "sha256:" + "d" * 64,
                "attested_by": "validation-owner",
                "attested_at": "2026-07-31T11:00:00Z",
                "authority_id": "validation-owner",
            },
            "outcome_access_attestation": {
                "status": "not_accessed",
                "occurred_at": "2026-07-31T11:01:00Z",
                "evidence_source_sha256": "sha256:" + "e" * 64,
                "attested_by": "validation-owner",
                "attested_at": "2026-07-31T11:01:00Z",
                "authority_id": "validation-owner",
            },
        }
        self.campaign_plan = {
            "study_id": "campaign-study",
            "platform": "meta",
            "platform_campaign_id": "platform-campaign-1",
            "platform_ad_group_id": "platform-adset-1",
            "platform_ad_id": "platform-ad-123",
            "platform_creative_id": "platform-ad-123",
            "block_id": "campaign-q3",
            "arm_id": "arm-a",
            "batch_id": "batch-one",
            "segment_ids": ["enterprise"],
            "creative_id": "creative-a",
            "variant_id": "creative-a",
            "asset_sha256": "sha256:" + "9" * 64,
        }
        self.supplied_facts["delivery_start_evidence"][
            "evidence_source_sha256"
        ] = sha256_json(self.campaign_plan)
        self._panel_binding = {
            "panel_id": "operations-leaders",
            "panel_version": "1-0-0",
            "panel_sha256": "sha256:" + "1" * 64,
            "package_sha256": "sha256:" + "2" * 64,
        }

    def _write(self, name: str, value: object) -> None:
        (self.run_root / name).write_bytes(canonical_json_bytes(value))

    def _write_run(self) -> None:
        roster = {
            "creatives": [
                {
                    "variation_id": "creative-a",
                    "display_name": "Acme proof",
                    "role": "proof challenger",
                    "content_hash": "sha256:" + "9" * 64,
                    "media": [],
                },
                {
                    "variation_id": "creative-b",
                    "display_name": "Acme control",
                    "role": "control",
                    "content_hash": "sha256:" + "a" * 64,
                    "media": [],
                },
            ]
        }
        result = {
            "study_id": "complete-acme-001",
            "method": "complete_exposure",
            "validity_status": "valid",
            "ranked_ids": ["creative-a", "creative-b"],
            "utilities": {"creative-a": 1.0, "creative-b": 0.0},
        }
        testing_map = {
            "testing_map": [
                {
                    "creative_id": "creative-a",
                    "role": "proof challenger",
                    "next_test": "Test the proof against the control.",
                },
                {
                    "creative_id": "creative-b",
                    "role": "control",
                    "next_test": "Retain as the control.",
                },
            ]
        }
        self._write("creative-roster.json", roster)
        self._write("screening-model-results.json", result)
        self._write("testing-map.json", testing_map)
        self._write(
            "study-manifest.json",
            {
                "study_id": "complete-acme-001",
                "method": "complete_exposure",
                "generated_at": "2026-07-30T12:00:00Z",
                "producer_evidence_sealed_at": "2026-07-30T12:01:00Z",
                "outputs": {
                    "creative_asset_hashes": {
                        "creative-a": "sha256:" + "9" * 64,
                        "creative-b": "sha256:" + "a" * 64,
                    }
                },
                "producer_bindings": {
                    "lineage_bundle_sha256": "sha256:" + "6" * 64,
                    "producer_evidence_sha256": "sha256:" + "7" * 64,
                    "producer_semantics_sha256": "sha256:" + "8" * 64,
                },
            },
        )

    def _producer_receipt(self) -> dict[str, object]:
        def binding(name: str) -> dict[str, object]:
            raw = canonical_json_bytes({"role": name})
            digest = "sha256:" + hashlib.sha256(raw).hexdigest()
            return {
                "path": f"{name}.json",
                "raw_bytes_sha256": digest,
                "canonical_document_sha256": digest,
                "record_count": None,
            }

        manifest_raw = (
            self.authenticated_snapshot_root / "study-manifest.json"
        ).read_bytes()
        result_raw = (
            self.authenticated_snapshot_root / "screening-model-results.json"
        ).read_bytes()
        manifest = json.loads(manifest_raw)
        result = json.loads(result_raw)
        inputs = {role: binding(role) for role in LINEAGE_ORDER}
        inputs["study_manifest"] = {
            "path": "study-manifest.json",
            "raw_bytes_sha256": "sha256:"
            + hashlib.sha256(manifest_raw).hexdigest(),
            "canonical_document_sha256": sha256_json(manifest),
            "record_count": None,
        }
        return {
            "schema_version": "panel-synthetic-producer-evidence-v1",
            "surface": "complete_exposure_ordering",
            "method": "complete_exposure",
            "stage": "screening",
            "run_id": "complete-acme-001",
            "frozen_at": "2026-07-30T12:00:00Z",
            "sealed_at": "2026-07-30T12:01:00Z",
            "producer_semantics": {
                "producer_semantics_sha256": "sha256:" + "8" * 64,
            },
            "input_bindings": inputs,
            "result_binding": {
                "path": "screening-model-results.json",
                "raw_bytes_sha256": "sha256:"
                + hashlib.sha256(result_raw).hexdigest(),
                "canonical_document_sha256": sha256_json(result),
                "record_count": None,
            },
            "snapshot_binding": {
                "snapshot_id": "snapshot-one",
                "snapshot_sha256": "sha256:" + "4" * 64,
                "archive_sha256": "sha256:" + "5" * 64,
            },
            "producer_evidence_sha256": "sha256:" + "7" * 64,
        }

    @contextmanager
    def _producer_snapshot(self):
        paths = {
            "study_manifest":
                self.authenticated_snapshot_root / "study-manifest.json",
            "result":
                self.authenticated_snapshot_root
                / "screening-model-results.json",
        }
        yield SimpleNamespace(
            snapshot_id="snapshot-one",
            snapshot_sha256="sha256:" + "4" * 64,
            archive_sha256="sha256:" + "5" * 64,
            frozen_at="2026-07-30T12:00:00Z",
            resolve_member=lambda role: paths[role],
        )

    def _draft(
        self,
        facts: dict[str, object] | None = None,
        campaign_plans: list[dict[str, object]] | None = None,
    ) -> RegistrationDraft:
        with (
            patch(
                "outcome_data_prep.registration._derive_panel_binding",
                return_value=self._panel_binding,
            ),
            patch(
                "outcome_data_prep.registration.validate_synthetic_producer_evidence",
                return_value=deepcopy(self.producer_receipt),
            ),
            patch(
                "outcome_data_prep.registration.open_evidence_snapshot",
                side_effect=lambda **_kwargs: self._producer_snapshot(),
            ),
        ):
            return build_registration_draft(
                run_root=self.run_root,
                panel_package=self.panel_package,
                campaign_plans=campaign_plans or [self.campaign_plan],
                supplied_facts=facts or self.supplied_facts,
            )

    def _registry(self, draft: RegistrationDraft) -> Path:
        root_sha = "sha256:" + hashlib.sha256(
            self.authority_root.read_bytes()
        ).hexdigest()
        index_sha = "sha256:" + hashlib.sha256(
            self.authority_index.read_bytes()
        ).hexdigest()
        document = authority_registry_document(
            draft.preregistration,
            root_sha256=root_sha,
            index_sha256=index_sha,
        )
        path = self.root / "trusted-authority-registry.json"
        path.write_bytes(canonical_json_bytes(document))
        return path

    def _seal(self, draft: RegistrationDraft, output: Path | None = None):
        registry = self._registry(draft)
        with (
            patch(
                "outcome_data_prep.registration.require_approved_runtime"
            ),
            patch.object(
                tier4_contracts,
                "_authority_secret_fingerprint_for_registry",
                return_value=AUTHORITY_SECRET_SHA256,
            ),
        ):
            return seal_study_registration(
                draft=draft,
                authority_root=self.authority_root,
                authority_index=self.authority_index,
                authority_registry=registry,
                authority_secret_file=self.authority_secret_file,
                output_dir=output or self.output,
            )

    def _authenticate(self, study: Path):
        registry = self.root / "trusted-authority-registry.json"
        with patch.object(
            tier4_contracts,
            "_authority_secret_fingerprint_for_registry",
            return_value=AUTHORITY_SECRET_SHA256,
        ):
            return authenticate_study_receipt(
                study_root=study,
                authority_registry=registry,
                authority_secret_file=self.authority_secret_file,
            )

    def _meta_import_source(
        self,
        *,
        campaign_id: str = "platform-campaign-1",
        adset_id: str = "platform-adset-1",
        ad_id: str = "platform-ad-123",
        outcome: str = "3",
        impressions: str = "1000",
        clicks: str = "12",
        spend: str = "123.45",
        name: str = "meta-export.json",
        export_timestamp: str = "2026-08-10T12:00:00Z",
        account_timezone: str = "America/New_York",
        report_date: str = "2026-08-10",
    ) -> tuple[Path, dict[str, object], dict[str, object]]:
        source = self.root / name
        source.write_bytes(canonical_json_bytes({
            "rows": [{
                "account_id": "act-123",
                "campaign_id": campaign_id,
                "adset_id": adset_id,
                "ad_id": ad_id,
                "date_start": report_date,
                "date_stop": report_date,
                "impressions": impressions,
                "clicks": clicks,
                "spend": spend,
                "actions": [
                    {"action_type": "link_click", "value": clicks},
                    {
                        "action_type": "offsite_conversion.purchase",
                        "value": outcome,
                    },
                ],
            }],
        }))
        governance = validate_source_governance_input({
            "schema_version": SOURCE_GOVERNANCE_INPUT_VERSION,
            "data_owner": "Acme data owner",
            "system_of_record": "Meta Ads",
            "permission_reference": "permission-one",
            "confirmer": "validation-owner",
            "allowed_purpose": "aggregate outcome validation",
            "retention_policy": "retain with authenticated study",
            "minimum_group_size_rule": "10",
            "restricted_fields_removed_attestation": True,
            "export_method": "aggregate platform export",
            "export_timestamp": export_timestamp,
            "source_governance_input_sha256": None,
        })
        context = {
            "adapter_registration": {
                "study_id": "campaign-study",
                "registration_id": "validation-q3",
                "metric_id": "qualified-response-rate",
                "conversion_event_key": "offsite_conversion.purchase",
                "request_level": "ad",
                "action_report_time": "conversion",
                "attribution_windows": ["7d_click", "1d_view"],
                "time_increment": "1",
            },
            "reporting_metadata": {
                "account_currency": "USD",
                "account_timezone": account_timezone,
                "request_level": "ad",
                "action_report_time": "conversion",
                "attribution_windows": ["7d_click", "1d_view"],
                "time_increment": "1",
                "reporting_basis": "account_reporting_day",
                "latency_state": "mature",
                "conversion_value_state": "observed",
                "observed_at": export_timestamp,
            },
        }
        return source, governance, context

    def _bound_context(
        self,
        *,
        source: Path,
        context: dict[str, object],
        sealed: object,
    ) -> dict[str, object]:
        staging = self.root / "context-bindings" / source.name
        snapshot = snapshot_source(source, staging_root=staging)
        inventory = inspect_container(snapshot)
        result = deepcopy(context)
        result["source_binding"] = {
            "source_sha256": snapshot.source_sha256,
            "inventory_sha256": container_inventory_sha256(inventory),
            "study_id": "campaign-study",
            "delivery_map_sha256": sealed.delivery_map_sha256,
        }
        return result

    def _source_input(
        self,
        source: Path,
        governance: dict[str, object],
        context: dict[str, object],
        sealed: object,
    ) -> SourceInput:
        return SourceInput(
            source,
            governance,
            self._bound_context(
                source=source, context=context, sealed=sealed
            ),
        )

    def _generic_import_source(
        self,
        *,
        delivery_map_sha256: str,
        name: str = "generic-export.csv",
        alternate_headers: bool = False,
    ) -> tuple[Path, dict[str, object], dict[str, object]]:
        headers = [
            "Campaign Code" if alternate_headers else "Campaign Key",
            "Line Code" if alternate_headers else "Line Key",
            "Ad Code" if alternate_headers else "Ad Key",
            "Creative Code" if alternate_headers else "Creative Key",
            "Report Day",
            "Delivered",
            "Click Count",
            "Media Spend",
            "Currency Code",
            "Approved Purchase",
        ]
        targets = (
            "campaign_id", "line_item_id", "ad_id", "creative_id", "date",
            "impressions", "clicks", "spend", "currency",
            "conversion_value",
        )
        mapping = dict(zip(headers, targets, strict=True))
        values = [
            "campaign-1", "line-1", "ad-1", "creative-1", "2026-08-10",
            "1000", "12", "123.45", "USD", "3",
        ]
        output = io.StringIO(newline="")
        writer = csv.writer(output, lineterminator="\n")
        writer.writerow(headers)
        writer.writerow(values)
        source = self.root / name
        source.write_text(output.getvalue(), encoding="utf-8", newline="")
        export_timestamp = "2026-08-10T12:00:00Z"
        governance = validate_source_governance_input({
            "schema_version": SOURCE_GOVERNANCE_INPUT_VERSION,
            "data_owner": "Acme data owner",
            "system_of_record": "Approved generic DSP export",
            "permission_reference": "permission-one",
            "confirmer": "validation-owner",
            "allowed_purpose": "aggregate outcome validation",
            "retention_policy": "retain with authenticated study",
            "minimum_group_size_rule": "10",
            "restricted_fields_removed_attestation": True,
            "export_method": "aggregate platform export",
            "export_timestamp": export_timestamp,
            "source_governance_input_sha256": None,
        })
        context = {
            "adapter_id": "generic-dsp-mapping-v1",
            "mapping": mapping,
            "delivery_map_sha256": delivery_map_sha256,
            "reporting_metadata": {
                "source_container": "csv",
                "source_platform": "generic_dsp",
                "headers": headers,
                "header_fingerprint": sha256_json(sorted(headers)),
                "mapping_profile_id": "approved-generic-profile-one",
                "stable_id_targets": [
                    "campaign_id", "line_item_id", "ad_id", "creative_id",
                ],
                "timezone": "UTC",
                "time_basis": "authenticated_source_reporting_day",
                "currency": "USD",
                "attribution_semantics": "registered_attribution_window",
                "attribution_windows": ["seven-days"],
                "conversion_metric": "Approved Purchase",
                "admitted_null_tokens": [],
                "null_value_state": "null",
                "aggregate_level": "already_aggregate",
                "currency_inferred": False,
                "currency_conversion": False,
                "cross_platform_reach_deduplication": False,
                "reconstructed_attribution": False,
                "platform_proof_basis": "declared_not_filename",
                "mixed_time_bases": False,
                "automatic_adapter_promotion": False,
                "conversion_value_state": "observed",
                "latency_state": "mature",
                "observed_at": export_timestamp,
                "omitted_zero_behavior": (
                    "omitted_metrics_are_unknown_not_zero"
                ),
            },
        }
        return source, governance, context

    def _run_meta_import(
        self,
        *,
        facts: dict[str, object] | None = None,
        import_id: str = "import-meta-one",
        output: Path | None = None,
    ):
        self.campaign_plan["platform"] = "meta_ads"
        import_facts = deepcopy(facts or self.supplied_facts)
        delivery_evidence = import_facts["delivery_start_evidence"]
        assert isinstance(delivery_evidence, dict)
        delivery_evidence["evidence_source_sha256"] = sha256_json(
            self.campaign_plan
        )
        sealed = self._seal(self._draft(import_facts), output=output)
        source, governance, context = self._meta_import_source()
        request = ImportRequest(
            study_root=sealed.study_root,
            sources=(self._source_input(
                source, governance, context, sealed
            ),),
            authority_registry=self.root / "trusted-authority-registry.json",
            authority_secret_file=self.authority_secret_file,
            imported_at="2026-08-10T12:01:00Z",
            import_id=import_id,
        )
        with (
            patch("outcome_data_prep.workflow.require_approved_runtime"),
            patch.object(
                tier4_contracts,
                "_authority_secret_fingerprint_for_registry",
                return_value=AUTHORITY_SECRET_SHA256,
            ),
        ):
            return import_results(request)

    def test_draft_derives_frozen_prediction_and_delivery_map(self):
        draft = self._draft()
        self.assertEqual(
            self.synthetic_result_sha256,
            draft.preregistration["synthetic_surface"]["result_sha256"],
        )
        self.assertEqual(
            "platform-ad-123",
            draft.delivery_map["mappings"][0]["platform_creative_id"],
        )
        self.assertEqual((), draft.unresolved_questions)

    def test_draft_calls_the_existing_authenticated_producer_seam(self):
        with (
            patch(
                "outcome_data_prep.registration._derive_panel_binding",
                return_value=self._panel_binding,
            ),
            patch(
                "outcome_data_prep.registration.validate_synthetic_producer_evidence",
                return_value=deepcopy(self.producer_receipt),
            ) as validate,
            patch(
                "outcome_data_prep.registration.open_evidence_snapshot",
                side_effect=lambda **_kwargs: self._producer_snapshot(),
            ) as snapshot,
        ):
            build_registration_draft(
                run_root=self.run_root,
                panel_package=self.panel_package,
                campaign_plans=[self.campaign_plan],
                supplied_facts=self.supplied_facts,
            )
        expected = {
            "surface": "complete_exposure_ordering",
            "run_id": "complete-acme-001",
            "result_sha256": self.synthetic_result_sha256,
            "evidence_root": self.producer_evidence_root,
            "snapshot_root": self.producer_snapshot_root,
        }
        validate.assert_called_once_with(**expected)
        snapshot.assert_called_once_with(
            surface=expected["surface"],
            run_id=expected["run_id"],
            result_sha256=expected["result_sha256"],
            snapshot_root=expected["snapshot_root"],
        )

    def test_draft_does_not_require_a_caller_built_tier4_template(self):
        facts = {
            key: deepcopy(value)
            for key, value in self.supplied_facts.items()
            if key != "preregistration_template"
        }
        facts["registration_id"] = "derived-registration"
        draft = self._draft(facts)
        self.assertEqual(
            "derived-registration", draft.preregistration["registration_id"]
        )
        self.assertEqual(
            self._panel_binding, draft.preregistration["panel_binding"]
        )
        self.assertEqual((), draft.unresolved_questions)
        sealed = self._seal(draft, self.root / "derived-study")
        authenticated, _authority = self._authenticate(sealed.study_root)
        self.assertEqual(
            "derived-registration",
            authenticated.registration["registration_id"],
        )

    def test_post_outcome_delivery_mapping_is_permanently_descriptive(self):
        facts = deepcopy(self.supplied_facts)
        facts["registered_at"] = "2026-07-31T12:00:00Z"
        facts["delivery_map_sealed_at"] = "2026-07-31T12:00:00Z"
        facts["first_outcome_accessed_at"] = "2026-07-31T11:00:00Z"
        draft = self._draft(facts)
        self.assertEqual("descriptive_only", draft.evidence_status)
        sealed = self._seal(draft)
        self.assertEqual("descriptive_only", sealed.evidence_status)

    def test_bare_editable_timestamp_cannot_establish_holdout_chronology(self):
        facts = {
            **self.supplied_facts,
            "delivery_started_at": "2026-07-31T11:00:00Z",
            "delivery_start_evidence": None,
        }
        self.assertEqual("descriptive_only", self._draft(facts).evidence_status)

    def test_missing_question_codes_are_exact_and_block_seal(self):
        facts = {**self.supplied_facts, "permission_reference": None}
        draft = self._draft(facts)
        self.assertEqual(("permission_reference",), draft.unresolved_questions)
        with self.assertRaisesRegex(StudyAuthorityError, "unresolved questions"):
            self._seal(draft)

    def test_seal_authenticates_exact_receipt_and_never_ingresses_secret(self):
        sealed = self._seal(self._draft())
        authenticated, authority = self._authenticate(sealed.study_root)
        self.assertEqual(sealed.registration_sha256, authenticated.registration["registration_sha256"])
        self.assertIsInstance(authority, StudyAuthority)
        secret = AUTHORITY_SECRET
        for path in sealed.study_root.rglob("*"):
            if path.is_file():
                self.assertNotIn(secret, path.read_bytes())
        with self.assertRaisesRegex(StudyAuthorityError, "only be minted"):
            StudyAuthority()

    def test_import_event_requires_the_authenticated_study_capability(self):
        sealed = self._seal(self._draft())
        authenticated, authority = self._authenticate(sealed.study_root)
        event = {
            "schema_version": "outcome-import-event-v1",
            "import_id": "import-one",
            "study_id": "campaign-study",
            "imported_at": "2026-08-01T12:00:00Z",
            "imported_by": "validation-owner",
            "source_manifest_sha256": "sha256:" + "b" * 64,
            "observation_ids": ["observation-one"],
            "import_event_sha256": None,
        }
        event["import_event_sha256"] = sha256_json(event)
        envelope = {
            "event": event,
            "event_hmac_sha256": authority_hmac(
                domain=IMPORT_EVENT_DOMAIN,
                payload=import_event_authority_projection(
                    event,
                    registration_id=str(
                        authenticated.registration["registration_id"]
                    ),
                    receipt_sha256=str(
                        authenticated.registration_receipt["receipt_sha256"]
                    ),
                ),
                secret=AUTHORITY_SECRET,
            ),
        }
        self.assertEqual(
            event,
            authenticate_import_event(envelope, authority=authority),
        )
        changed = deepcopy(envelope)
        changed["event"]["imported_at"] = "2026-08-01T12:01:00Z"
        with self.assertRaises(StudyAuthorityError):
            authenticate_import_event(changed, authority=authority)

    def test_import_event_cannot_replay_across_same_study_registrations(self):
        first = self._seal(self._draft())
        first_study, first_authority = self._authenticate(first.study_root)

        facts = deepcopy(self.supplied_facts)
        template = facts["preregistration_template"]
        assert isinstance(template, dict)
        template["registration_id"] = "validation-q3-second"
        template["multiplicity_rules"]["family_id"] = "family-q3-second"
        template["multiplicity_rules"]["member_registration_ids"] = [
            "validation-q3-second"
        ]
        second = self._seal(
            self._draft(facts), self.root / "second-study"
        )
        _second_study, second_authority = self._authenticate(second.study_root)

        event = {
            "schema_version": "outcome-import-event-v1",
            "import_id": "import-one",
            "study_id": "campaign-study",
            "imported_at": "2026-08-01T12:00:00Z",
            "imported_by": "validation-owner",
            "source_manifest_sha256": "sha256:" + "b" * 64,
            "observation_ids": ["observation-one"],
            "import_event_sha256": None,
        }
        event["import_event_sha256"] = sha256_json(event)
        envelope = {
            "event": event,
            "event_hmac_sha256": authority_hmac(
                domain=IMPORT_EVENT_DOMAIN,
                payload=import_event_authority_projection(
                    event,
                    registration_id=str(
                        first_study.registration["registration_id"]
                    ),
                    receipt_sha256=str(
                        first_study.registration_receipt["receipt_sha256"]
                    ),
                ),
                secret=AUTHORITY_SECRET,
            ),
        }
        self.assertEqual(
            event,
            authenticate_import_event(envelope, authority=first_authority),
        )
        self.assertNotEqual(
            first_study.registration["registration_id"],
            _second_study.registration["registration_id"],
        )
        with self.assertRaisesRegex(StudyAuthorityError, "authentication"):
            authenticate_import_event(envelope, authority=second_authority)

    def test_delivery_map_tamper_breaks_study_authentication(self):
        study = self._seal(self._draft()).study_root
        path = study / "delivery-map.json"
        value = json.loads(path.read_text(encoding="utf-8"))
        value["mappings"][0]["platform_creative_id"] = "attacker-selected-id"
        value["delivery_map_sha256"] = None
        value["delivery_map_sha256"] = sha256_json(value)
        path.write_bytes(canonical_json_bytes(value))
        with self.assertRaisesRegex(StudyAuthorityError, "authenticated study receipt"):
            self._authenticate(study)

    def test_chronology_tamper_breaks_study_authentication(self):
        study = self._seal(self._draft()).study_root
        path = study / "registration-receipt.json"
        value = json.loads(path.read_text(encoding="utf-8"))
        value["chronology"]["events"][1]["occurred_at"] = "2026-07-30T08:00:00Z"
        value["receipt_sha256"] = None
        value["receipt_sha256"] = sha256_json({
            **value,
            "receipt_sha256": None,
            "receipt_hmac_sha256": None,
        })
        path.write_bytes(canonical_json_bytes(value))
        with self.assertRaisesRegex(StudyAuthorityError, "receipt authentication"):
            self._authenticate(study)

    def test_conflicting_chronology_evidence_blocks_seal(self):
        facts = deepcopy(self.supplied_facts)
        first = facts["delivery_start_evidence"]
        assert isinstance(first, dict)
        facts["delivery_start_evidence"] = [
            first,
            {**first, "occurred_at": "2026-07-31T11:30:00Z"},
        ]
        with self.assertRaisesRegex(StudyAuthorityError, "chronology evidence"):
            self._seal(self._draft(facts))

    def test_outcome_not_accessed_before_authoritative_sequence_is_blocked(self):
        facts = deepcopy(self.supplied_facts)
        outcome = facts["outcome_access_attestation"]
        assert isinstance(outcome, dict)
        outcome["occurred_at"] = "2026-07-31T09:00:00Z"
        outcome["attested_at"] = "2026-07-31T09:00:00Z"
        draft = self._draft(facts)
        self.assertEqual("descriptive_only", draft.evidence_status)
        with self.assertRaisesRegex(StudyAuthorityError, "chronology evidence"):
            self._seal(draft)

    def test_unrelated_delivery_evidence_digest_cannot_authorize_holdout(self):
        facts = deepcopy(self.supplied_facts)
        evidence = facts["delivery_start_evidence"]
        assert isinstance(evidence, dict)
        evidence["evidence_source_sha256"] = "sha256:" + "f" * 64
        draft = self._draft(facts)
        self.assertEqual("blocked", draft.evidence_status)
        with self.assertRaisesRegex(StudyAuthorityError, "chronology evidence"):
            self._seal(draft)

    def test_coherent_forged_producer_files_fail_authenticated_snapshot(self):
        manifest_path = self.run_root / "study-manifest.json"
        result_path = self.run_root / "screening-model-results.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        result = json.loads(result_path.read_text(encoding="utf-8"))
        manifest["study_id"] = "forged-run"
        result["study_id"] = "forged-run"
        manifest_path.write_bytes(canonical_json_bytes(manifest))
        result_path.write_bytes(canonical_json_bytes(result))
        with self.assertRaisesRegex(ContractError, "authenticated producer"):
            self._draft()

    def test_output_collision_is_non_destructive(self):
        draft = self._draft()
        self._seal(draft)
        before = {
            path.name: path.read_bytes()
            for path in self.output.iterdir()
            if path.is_file()
        }
        with self.assertRaisesRegex(StudyAuthorityError, "already exists"):
            self._seal(draft)
        self.assertEqual(
            before,
            {
                path.name: path.read_bytes()
                for path in self.output.iterdir()
                if path.is_file()
            },
        )

    def test_preregistered_schema_tested_import_is_incomplete_not_ready(self):
        result = self._run_meta_import()
        self.assertEqual("preregistered_holdout", result.evidence_status)
        self.assertEqual("incomplete", result.operational_status)
        self.assertTrue(result.validation_handoff_written)
        readiness = json.loads(result.readiness_report_json.read_text())
        self.assertIn("export verification", " ".join(readiness["reasons"]))
        self.assertTrue(result.generation_path.exists())

    def test_historical_import_is_permanently_descriptive(self):
        facts = deepcopy(self.supplied_facts)
        facts["registered_at"] = "2026-07-31T12:00:00Z"
        facts["delivery_map_sealed_at"] = "2026-07-31T12:00:00Z"
        facts["first_outcome_accessed_at"] = "2026-07-31T11:00:00Z"
        result = self._run_meta_import(
            facts=facts,
            import_id="import-meta-historical",
        )
        self.assertEqual("descriptive_only", result.evidence_status)
        self.assertEqual("descriptive_only", result.operational_status)
        self.assertFalse(result.validation_handoff_written)
        self.assertFalse(
            (result.generation_path / "validation-handoff.json").exists()
        )

    def _assert_authenticated_downgrade_correction(
        self, effective_status: str
    ) -> None:
        self.campaign_plan["platform"] = "meta_ads"
        facts = deepcopy(self.supplied_facts)
        delivery_evidence = facts["delivery_start_evidence"]
        assert isinstance(delivery_evidence, dict)
        delivery_evidence["evidence_source_sha256"] = sha256_json(
            self.campaign_plan
        )
        sealed = self._seal(self._draft(facts))

        source, governance, context = self._meta_import_source(
            name="meta-downgrade-seed.json",
        )
        seed_request = ImportRequest(
            study_root=sealed.study_root,
            sources=(self._source_input(
                source, governance, context, sealed
            ),),
            authority_registry=self.root / "trusted-authority-registry.json",
            authority_secret_file=self.authority_secret_file,
            imported_at="2026-08-10T12:01:00Z",
            import_id="import-downgrade-seed",
        )

        def commit_with_effective_status(**kwargs):
            stage = Path(kwargs["staged_generation"])
            manifest_path = stage / "generation-manifest.json"
            manifest = json.loads(manifest_path.read_bytes())
            handoff_paths = [
                stage / item["relative_path"]
                for item in manifest["files"]
                if item["role"] == "validation_handoff"
            ]
            self.assertEqual(1, len(handoff_paths))
            handoff_paths[0].unlink()
            manifest["files"] = [
                item for item in manifest["files"]
                if item["role"] != "validation_handoff"
            ]
            normalized_path = stage / "normalized-observations.json"
            normalized = json.loads(normalized_path.read_bytes())
            normalized_by_id = {}
            for row in normalized:
                row["validation_projection"]["evidence_status"] = (
                    effective_status
                )
                row["validation_projection"]["assignment"]["design"] = (
                    "observational"
                )
                row["normalized_observation_sha256"] = None
                row["normalized_observation_sha256"] = sha256_json(row)
                normalized_by_id[row["observation_id"]] = row
            normalized_raw = canonical_json_bytes(normalized)
            normalized_path.write_bytes(normalized_raw)

            bindings_path = stage / "observation-bindings.json"
            bindings = json.loads(bindings_path.read_bytes())
            for row in bindings:
                row["evidence_status"] = effective_status
                row["normalized_observation_sha256"] = normalized_by_id[
                    row["observation_id"]
                ]["normalized_observation_sha256"]
                row["observation_binding_sha256"] = None
                row["observation_binding_sha256"] = sha256_json(row)
            bindings_raw = canonical_json_bytes(bindings)
            bindings_path.write_bytes(bindings_raw)
            replacements = {
                "normalized-observations.json": normalized_raw,
                "observation-bindings.json": bindings_raw,
            }
            for item in manifest["files"]:
                replacement = replacements.get(item["relative_path"])
                if replacement is not None:
                    item["sha256"] = sha256_bytes(replacement)
                    item["byte_count"] = len(replacement)
            manifest["next_evidence_status"] = effective_status
            manifest["validation_handoff_sha256"] = None
            manifest["generation_sha256"] = None
            manifest["generation_sha256"] = sha256_json(manifest)
            manifest_path.write_bytes(canonical_json_bytes(manifest))
            return commit_import_generation(**kwargs)

        with (
            patch("outcome_data_prep.workflow.require_approved_runtime"),
            patch.object(
                tier4_contracts,
                "_authority_secret_fingerprint_for_registry",
                return_value=AUTHORITY_SECRET_SHA256,
            ),
            patch(
                "outcome_data_prep.workflow.commit_import_generation",
                side_effect=commit_with_effective_status,
            ),
        ):
            seed_result = import_results(seed_request)

        _study, replay_authority = self._authenticate(sealed.study_root)
        replayed = replay_authenticated_ledger(
            sealed.study_root, authority=replay_authority
        )
        self.assertEqual(effective_status, replayed.current_evidence_status)

        source, governance, context = self._meta_import_source(
            name="meta-after-downgrade.json",
            outcome="4",
        )
        request = ImportRequest(
            study_root=sealed.study_root,
            sources=(self._source_input(
                source, governance, context, sealed
            ),),
            authority_registry=self.root / "trusted-authority-registry.json",
            authority_secret_file=self.authority_secret_file,
            imported_at="2026-08-10T12:02:00Z",
            import_id="import-after-downgrade",
        )
        prior_event = json.loads(
            (seed_result.generation_path / "import-event.json").read_text()
        )
        correction = CorrectionInput(
            correction_id="correction-after-downgrade",
            requested_at="2026-08-10T12:01:30Z",
            actor="validation-owner",
            reason_code="measurement_correction",
            reason="Platform issued corrected aggregate outcome values.",
            supersedes_import_id=seed_result.import_id,
            supersedes_observation_ids=tuple(prior_event["observation_ids"]),
            expected_analytical_identity_sha256=(
                seed_result.analytical_identity_sha256
            ),
        )
        captured = {}

        def capture_complete_stage(**kwargs):
            staged = validate_complete_staged_generation(
                Path(kwargs["staged_generation"]),
                authority=kwargs["authority"],
            )
            captured["handoff"] = staged.handoff
            self.assertIsNone(staged.handoff)
            return commit_import_generation(**kwargs)

        with (
            patch("outcome_data_prep.workflow.require_approved_runtime"),
            patch.object(
                tier4_contracts,
                "_authority_secret_fingerprint_for_registry",
                return_value=AUTHORITY_SECRET_SHA256,
            ),
            patch(
                "outcome_data_prep.workflow.commit_import_generation",
                side_effect=capture_complete_stage,
            ),
        ):
            result = import_results(request, correction)

        self.assertIn("handoff", captured)
        self.assertEqual(effective_status, result.evidence_status)
        self.assertEqual(effective_status, result.operational_status)
        self.assertFalse(result.validation_handoff_written)
        self.assertFalse(
            (result.generation_path / "validation-handoff.json").exists()
        )
        normalized = json.loads(
            (result.generation_path / "normalized-observations.json").read_text()
        )
        bindings = json.loads(
            (result.generation_path / "observation-bindings.json").read_text()
        )
        self.assertEqual(
            {effective_status},
            {
                row["validation_projection"]["evidence_status"]
                for row in normalized
            },
        )
        self.assertEqual(
            {effective_status},
            {row["evidence_status"] for row in bindings},
        )
        self.assertTrue(
            (result.generation_path / "correction-request.json").exists()
        )
        _study, final_authority = self._authenticate(sealed.study_root)
        final_state = replay_authenticated_ledger(
            sealed.study_root, authority=final_authority
        )
        self.assertEqual(
            effective_status, final_state.current_evidence_status
        )

    def test_authenticated_downgrade_cannot_reuse_stale_receipt_handoff(self):
        self._assert_authenticated_downgrade_correction("descriptive_only")

    def test_authenticated_block_cannot_reuse_stale_receipt_handoff(self):
        self._assert_authenticated_downgrade_correction("blocked")

    def test_readiness_reports_use_the_four_literal_headings_and_boundary(self):
        cases = {
            "contract_ready": ("preregistered_holdout", "export_verified"),
            "incomplete": ("preregistered_holdout", "schema_tested"),
            "descriptive_only": ("descriptive_only", "schema_tested"),
            "blocked": ("blocked", "blocked"),
        }
        for status, (evidence, maturity) in cases.items():
            with self.subTest(status=status):
                report = validate_readiness_report({
                    "schema_version": READINESS_VERSION,
                    "study_id": "campaign-study",
                    "import_id": "import-one",
                    "evidence_status": evidence,
                    "operational_status": status,
                    "adapter_maturity": maturity,
                    "reasons": ["reason-code"],
                    "readiness_sha256": None,
                })
                rendered = render_readiness_report(report)
                self.assertTrue(rendered.startswith(
                    f"# {READINESS_HEADINGS[status]}\n"
                ))
                self.assertIn("does not decide whether the panel was right", rendered)
                self.assertIn("does not change any persona or active panel", rendered)

    def test_source_governance_and_context_arguments_are_exact_index_pairs(self):
        paired = pair_source_arguments(
            [Path("one.csv"), Path("two.csv")],
            [Path("one-governance.json"), Path("two-governance.json")],
            [Path("one-context.json"), Path("two-context.json")],
        )
        self.assertEqual(
            Path("two-context.json"), paired[1][2]
        )
        for governance, context in (
            ([Path("one.json")], []),
            ([Path("one.json")], [Path("one.json"), Path("extra.json")]),
            ([], [Path("one.json"), Path("two.json")]),
        ):
            with self.subTest(governance=governance, context=context):
                with self.assertRaisesRegex(ValueError, "exact index pairs"):
                    pair_source_arguments(
                        [Path("one.csv"), Path("two.csv")],
                        governance,
                        context,
                    )

    def test_person_level_source_leaves_only_redacted_rejection_receipt(self):
        self.campaign_plan["platform"] = "meta_ads"
        facts = deepcopy(self.supplied_facts)
        facts["delivery_start_evidence"]["evidence_source_sha256"] = (
            sha256_json(self.campaign_plan)
        )
        sealed = self._seal(self._draft(facts))
        unsafe = self.root / "unsafe.csv"
        unsafe.write_text(
            "email,campaign_id,impressions\n"
            "person@example.com,platform-campaign-1,100\n",
            encoding="utf-8",
        )
        _safe, governance, context = self._meta_import_source()
        request = ImportRequest(
            study_root=sealed.study_root,
            sources=(self._source_input(
                unsafe, governance, context, sealed
            ),),
            authority_registry=self.root / "trusted-authority-registry.json",
            authority_secret_file=self.authority_secret_file,
            imported_at="2026-08-10T12:01:00Z",
            import_id="import-rejected-person-level",
        )
        with (
            patch("outcome_data_prep.workflow.require_approved_runtime"),
            patch.object(
                tier4_contracts,
                "_authority_secret_fingerprint_for_registry",
                return_value=AUTHORITY_SECRET_SHA256,
            ),
            self.assertRaises(ImportSafetyError) as raised,
        ):
            import_results(request)
        receipt = raised.exception.rejection_receipts[0]
        self.assertEqual(sealed.study_root / "rejections", receipt.parent)
        receipt_text = receipt.read_text(encoding="utf-8")
        self.assertNotIn("person@example.com", receipt_text)
        self.assertNotIn("campaign_id", receipt_text)
        self.assertIn("person_level", receipt_text)
        self.assertFalse((sealed.study_root / "imports").exists())

    def test_privacy_bearing_csv_filename_never_enters_durable_outputs(self):
        plan = deepcopy(self.campaign_plan)
        plan.update({
            "platform": "generic_dsp",
            "platform_campaign_id": "campaign-1",
            "platform_ad_group_id": "line-1",
            "platform_ad_id": "ad-1",
            "platform_creative_id": "creative-1",
        })
        facts = deepcopy(self.supplied_facts)
        facts["delivery_start_evidence"]["evidence_source_sha256"] = (
            sha256_json(plan)
        )
        sealed = self._seal(self._draft(facts, [plan]))
        source, governance, context = self._generic_import_source(
            delivery_map_sha256=sealed.delivery_map_sha256,
            name="person@example.com.csv",
        )
        request = ImportRequest(
            study_root=sealed.study_root,
            sources=(self._source_input(
                source, governance, context, sealed
            ),),
            authority_registry=self.root / "trusted-authority-registry.json",
            authority_secret_file=self.authority_secret_file,
            imported_at="2026-08-10T12:01:00Z",
            import_id="import-rejected-private-filename",
        )
        with (
            patch("outcome_data_prep.workflow.require_approved_runtime"),
            patch.object(
                tier4_contracts,
                "_authority_secret_fingerprint_for_registry",
                return_value=AUTHORITY_SECRET_SHA256,
            ),
            self.assertRaises(ImportSafetyError) as raised,
        ):
            import_results(request)
        self.assertIn("email", raised.exception.reason_codes)
        self.assertFalse((sealed.study_root / "imports").exists())
        durable_text = "\n".join(
            path.read_text(encoding="utf-8")
            for path in sealed.study_root.rglob("*.json")
        )
        self.assertNotIn("person@example.com", durable_text)
        self.assertNotIn("person@example.com.csv:2", durable_text)

    def test_root_swap_after_authentication_rejects_without_writes_or_fd_leaks(self):
        self.campaign_plan["platform"] = "meta_ads"
        facts = deepcopy(self.supplied_facts)
        facts["delivery_start_evidence"]["evidence_source_sha256"] = (
            sha256_json(self.campaign_plan)
        )
        sealed = self._seal(self._draft(facts))
        unsafe = self.root / "unsafe-root-swap.csv"
        unsafe.write_text("email\nprivate@example.com\n", encoding="utf-8")
        _safe, governance, context = self._meta_import_source()
        request = ImportRequest(
            study_root=sealed.study_root,
            sources=(self._source_input(
                unsafe, governance, context, sealed
            ),),
            authority_registry=self.root / "trusted-authority-registry.json",
            authority_secret_file=self.authority_secret_file,
            imported_at="2026-08-10T12:01:00Z",
            import_id="import-root-swap",
        )
        def tree_state(root: Path) -> dict[str, tuple[int, bytes | None]]:
            return {
                path.relative_to(root).as_posix(): (
                    path.lstat().st_mode,
                    path.read_bytes() if path.is_file() else None,
                )
                for path in sorted(root.rglob("*"))
            }

        authenticated_state_before = tree_state(sealed.study_root)
        moved = self.root / "authenticated-root-moved"
        replacement_sentinel = b"replacement root must remain unchanged"
        replacement_mode: int | None = None

        def authenticate_then_swap(**kwargs):
            nonlocal replacement_mode
            authenticated = authenticate_study_receipt(**kwargs)
            sealed.study_root.rename(moved)
            sealed.study_root.mkdir(mode=0o700)
            sealed.study_root.chmod(0o700)
            sentinel = sealed.study_root / "sentinel.bin"
            sentinel.write_bytes(replacement_sentinel)
            sentinel.chmod(0o600)
            replacement_mode = sealed.study_root.stat().st_mode
            return authenticated

        real_open = __import__("os").open
        real_close = __import__("os").close
        active: set[int] = set()

        def tracked_open(*args, **kwargs):
            descriptor = real_open(*args, **kwargs)
            active.add(descriptor)
            return descriptor

        def tracked_close(descriptor):
            try:
                real_close(descriptor)
            finally:
                active.discard(descriptor)

        with (
            patch("outcome_data_prep.workflow.require_approved_runtime"),
            patch(
                "outcome_data_prep.workflow.authenticate_study_receipt",
                side_effect=authenticate_then_swap,
            ),
            patch("outcome_data_prep.workflow.os.open", side_effect=tracked_open),
            patch("outcome_data_prep.workflow.os.close", side_effect=tracked_close),
            patch.object(
                tier4_contracts,
                "_authority_secret_fingerprint_for_registry",
                return_value=AUTHORITY_SECRET_SHA256,
            ),
            self.assertRaisesRegex(ImportConflict, "path identity changed"),
        ):
            import_results(request)
        self.assertEqual(set(), active)
        self.assertEqual(replacement_mode, sealed.study_root.stat().st_mode)
        self.assertEqual(
            replacement_sentinel,
            (sealed.study_root / "sentinel.bin").read_bytes(),
        )
        self.assertEqual(
            [sealed.study_root / "sentinel.bin"],
            list(sealed.study_root.iterdir()),
        )
        self.assertFalse((moved / "rejections").exists())
        self.assertFalse((moved / "imports").exists())
        self.assertEqual(authenticated_state_before, tree_state(moved))

    def test_five_impression_minimum_ten_late_gate_writes_redacted_receipt(self):
        self.campaign_plan["platform"] = "meta_ads"
        facts = deepcopy(self.supplied_facts)
        facts["delivery_start_evidence"]["evidence_source_sha256"] = (
            sha256_json(self.campaign_plan)
        )
        sealed = self._seal(self._draft(facts))
        source, governance, context = self._meta_import_source(
            impressions="5", name="five-impressions.json"
        )
        request = ImportRequest(
            study_root=sealed.study_root,
            sources=(self._source_input(
                source, governance, context, sealed
            ),),
            authority_registry=self.root / "trusted-authority-registry.json",
            authority_secret_file=self.authority_secret_file,
            imported_at="2026-08-10T12:01:00Z",
            import_id="import-five-impressions",
        )
        with (
            patch("outcome_data_prep.workflow.require_approved_runtime"),
            patch.object(
                tier4_contracts,
                "_authority_secret_fingerprint_for_registry",
                return_value=AUTHORITY_SECRET_SHA256,
            ),
            self.assertRaises(ImportSafetyError) as raised,
        ):
            import_results(request)
        self.assertEqual(
            ("minimum_group_size_below_rule",),
            raised.exception.reason_codes,
        )
        receipt = json.loads(
            raised.exception.rejection_receipts[0].read_text()
        )
        self.assertEqual(1, receipt["row_count"])
        self.assertIn("receipt_hmac_sha256", receipt)
        forbidden = (
            "headers", "values", "source_path", "person_id", "device_id",
            "raw_exception",
        )
        receipt_text = json.dumps(receipt)
        for value in forbidden:
            self.assertNotIn(value, receipt_text)
        self.assertFalse((sealed.study_root / "imports").exists())

    def test_late_prohibited_field_rejection_uses_the_same_receipt_path(self):
        self.campaign_plan["platform"] = "meta_ads"
        facts = deepcopy(self.supplied_facts)
        facts["delivery_start_evidence"]["evidence_source_sha256"] = (
            sha256_json(self.campaign_plan)
        )
        sealed = self._seal(self._draft(facts))
        source, governance, context = self._meta_import_source(
            name="late-prohibited.json"
        )
        request = ImportRequest(
            study_root=sealed.study_root,
            sources=(self._source_input(
                source, governance, context, sealed
            ),),
            authority_registry=self.root / "trusted-authority-registry.json",
            authority_secret_file=self.authority_secret_file,
            imported_at="2026-08-10T12:01:00Z",
            import_id="import-late-prohibited",
        )
        rejected = AdapterValidation(
            accepted=False,
            errors=("prohibited_field",),
            warnings=(),
            observed_minimum_group_size=1000,
            inventory_sha256="sha256:" + "0" * 64,
        )
        with (
            patch("outcome_data_prep.workflow.require_approved_runtime"),
            patch.object(
                tier4_contracts,
                "_authority_secret_fingerprint_for_registry",
                return_value=AUTHORITY_SECRET_SHA256,
            ),
            patch(
                "outcome_data_prep.workflow.MetaInsightsAdapter.validate",
                return_value=rejected,
            ),
            self.assertRaises(ImportSafetyError) as raised,
        ):
            import_results(request)
        self.assertEqual(("prohibited_field",), raised.exception.reason_codes)
        self.assertTrue(raised.exception.rejection_receipts[0].is_file())
        self.assertFalse((sealed.study_root / "imports").exists())

    def test_symlinked_rejection_parent_is_never_followed_or_modified(self):
        self.campaign_plan["platform"] = "meta_ads"
        facts = deepcopy(self.supplied_facts)
        facts["delivery_start_evidence"]["evidence_source_sha256"] = (
            sha256_json(self.campaign_plan)
        )
        sealed = self._seal(self._draft(facts))
        external = self.root / "external-rejections"
        external.mkdir(mode=0o755)
        sentinel = external / "sentinel.bin"
        sentinel.write_bytes(b"outside must remain byte-identical")
        before_mode = external.stat().st_mode
        (sealed.study_root / "rejections").symlink_to(
            external, target_is_directory=True
        )
        unsafe = self.root / "unsafe-symlink-probe.csv"
        unsafe.write_text("email\nprivate@example.com\n", encoding="utf-8")
        _safe, governance, context = self._meta_import_source()
        request = ImportRequest(
            study_root=sealed.study_root,
            sources=(self._source_input(
                unsafe, governance, context, sealed
            ),),
            authority_registry=self.root / "trusted-authority-registry.json",
            authority_secret_file=self.authority_secret_file,
            imported_at="2026-08-10T12:01:00Z",
            import_id="import-symlink-parent",
        )
        with (
            patch("outcome_data_prep.workflow.require_approved_runtime"),
            patch.object(
                tier4_contracts,
                "_authority_secret_fingerprint_for_registry",
                return_value=AUTHORITY_SECRET_SHA256,
            ),
            self.assertRaises(ImportConflict),
        ):
            import_results(request)
        self.assertEqual(before_mode, external.stat().st_mode)
        self.assertEqual(
            b"outside must remain byte-identical", sentinel.read_bytes()
        )
        self.assertEqual([sentinel], list(external.iterdir()))

    def test_rejection_receipt_collision_is_non_destructive(self):
        self.campaign_plan["platform"] = "meta_ads"
        facts = deepcopy(self.supplied_facts)
        facts["delivery_start_evidence"]["evidence_source_sha256"] = (
            sha256_json(self.campaign_plan)
        )
        sealed = self._seal(self._draft(facts))
        unsafe = self.root / "unsafe-collision.csv"
        unsafe.write_text("email\nprivate@example.com\n", encoding="utf-8")
        _safe, governance, context = self._meta_import_source()
        request = ImportRequest(
            study_root=sealed.study_root,
            sources=(self._source_input(
                unsafe, governance, context, sealed
            ),),
            authority_registry=self.root / "trusted-authority-registry.json",
            authority_secret_file=self.authority_secret_file,
            imported_at="2026-08-10T12:01:00Z",
            import_id="import-receipt-collision",
        )
        patches = (
            patch("outcome_data_prep.workflow.require_approved_runtime"),
            patch.object(
                tier4_contracts,
                "_authority_secret_fingerprint_for_registry",
                return_value=AUTHORITY_SECRET_SHA256,
            ),
        )
        with patches[0], patches[1], self.assertRaises(
            ImportSafetyError
        ) as first:
            import_results(request)
        receipt = first.exception.rejection_receipts[0]
        before = receipt.read_bytes()
        with (
            patch("outcome_data_prep.workflow.require_approved_runtime"),
            patch.object(
                tier4_contracts,
                "_authority_secret_fingerprint_for_registry",
                return_value=AUTHORITY_SECRET_SHA256,
            ),
            self.assertRaisesRegex(ImportConflict, "already exists"),
        ):
            import_results(request)
        self.assertEqual(before, receipt.read_bytes())

    def test_rejection_publication_closes_every_opened_descriptor(self):
        self.campaign_plan["platform"] = "meta_ads"
        facts = deepcopy(self.supplied_facts)
        facts["delivery_start_evidence"]["evidence_source_sha256"] = (
            sha256_json(self.campaign_plan)
        )
        sealed = self._seal(self._draft(facts))
        unsafe = self.root / "unsafe-descriptor.csv"
        unsafe.write_text("email\nprivate@example.com\n", encoding="utf-8")
        _safe, governance, context = self._meta_import_source()
        request = ImportRequest(
            study_root=sealed.study_root,
            sources=(self._source_input(
                unsafe, governance, context, sealed
            ),),
            authority_registry=self.root / "trusted-authority-registry.json",
            authority_secret_file=self.authority_secret_file,
            imported_at="2026-08-10T12:01:00Z",
            import_id="import-descriptor-closure",
        )
        real_open = __import__("os").open
        real_close = __import__("os").close
        active: set[int] = set()

        def tracked_open(*args, **kwargs):
            descriptor = real_open(*args, **kwargs)
            active.add(descriptor)
            return descriptor

        def tracked_close(descriptor):
            try:
                real_close(descriptor)
            finally:
                active.discard(descriptor)

        with (
            patch("outcome_data_prep.workflow.require_approved_runtime"),
            patch.object(
                tier4_contracts,
                "_authority_secret_fingerprint_for_registry",
                return_value=AUTHORITY_SECRET_SHA256,
            ),
            patch("outcome_data_prep.workflow.os.open", side_effect=tracked_open),
            patch("outcome_data_prep.workflow.os.close", side_effect=tracked_close),
            self.assertRaises(ImportSafetyError),
        ):
            import_results(request)
        self.assertEqual(set(), active)

    def test_correction_preserves_identity_and_prior_generation(self):
        first = self._run_meta_import(import_id="import-original")
        source, governance, context = self._meta_import_source(
            outcome="4", name="meta-corrected.json"
        )
        prior_event = json.loads(
            (first.generation_path / "import-event.json").read_text()
        )
        request = ImportRequest(
            study_root=self.output,
            sources=(self._source_input(
                source, governance, context,
                SimpleNamespace(
                    delivery_map_sha256=json.loads(
                        (self.output / "delivery-map.json").read_text()
                    )["delivery_map_sha256"]
                ),
            ),),
            authority_registry=self.root / "trusted-authority-registry.json",
            authority_secret_file=self.authority_secret_file,
            imported_at="2026-08-10T12:02:00Z",
            import_id="import-corrected",
        )
        correction = CorrectionInput(
            correction_id="correction-one",
            requested_at="2026-08-10T12:01:30Z",
            actor="validation-owner",
            reason_code="measurement_correction",
            reason="Platform issued corrected aggregate outcome values.",
            supersedes_import_id=first.import_id,
            supersedes_observation_ids=tuple(prior_event["observation_ids"]),
            expected_analytical_identity_sha256=(
                first.analytical_identity_sha256
            ),
        )
        with (
            patch("outcome_data_prep.workflow.require_approved_runtime"),
            patch.object(
                tier4_contracts,
                "_authority_secret_fingerprint_for_registry",
                return_value=AUTHORITY_SECRET_SHA256,
            ),
        ):
            corrected = import_results(request, correction)
        self.assertTrue(first.generation_path.exists())
        self.assertNotEqual(first.import_digest, corrected.import_digest)
        self.assertEqual(
            first.analytical_identity_sha256,
            corrected.analytical_identity_sha256,
        )
        document = json.loads(
            (corrected.generation_path / "correction-request.json").read_text()
        )
        self.assertEqual("measurement_correction", document["reason_code"])

    def test_observed_value_and_provenance_corrections_all_commit(self):
        cases = (
            ("impressions", {"impressions": "1100"}, False),
            ("clicks", {"clicks": "13"}, False),
            ("spend", {"spend": "124.50"}, False),
            ("outcome", {"outcome": "4"}, False),
            ("source-numeric-text", {"outcome": "3.0"}, False),
            ("value-state", {"outcome": "0"}, False),
            ("derived-tier4-counts", {"impressions": "900"}, False),
            ("source-provenance", {}, True),
        )
        for label, source_changes, rewrite_bytes in cases:
            with self.subTest(label=label):
                study_root = self.root / f"correction-study-{label}"
                original = self._run_meta_import(
                    import_id=f"import-{label}-original",
                    output=study_root,
                )
                prior_event = json.loads(
                    (
                        original.generation_path / "import-event.json"
                    ).read_text()
                )
                source, governance, context = self._meta_import_source(
                    name=f"replacement-{label}.json",
                    **source_changes,
                )
                if rewrite_bytes:
                    source.write_text(
                        json.dumps(json.loads(source.read_text()), indent=2),
                        encoding="utf-8",
                    )
                sealed = SimpleNamespace(
                    delivery_map_sha256=json.loads(
                        (study_root / "delivery-map.json").read_text()
                    )["delivery_map_sha256"]
                )
                request = ImportRequest(
                    study_root=study_root,
                    sources=(self._source_input(
                        source, governance, context, sealed
                    ),),
                    authority_registry=(
                        self.root / "trusted-authority-registry.json"
                    ),
                    authority_secret_file=self.authority_secret_file,
                    imported_at="2026-08-10T12:02:00Z",
                    import_id=f"import-{label}-corrected",
                )
                correction = CorrectionInput(
                    correction_id=f"correction-{label}",
                    requested_at="2026-08-10T12:01:30Z",
                    actor="validation-owner",
                    reason_code="measurement_correction",
                    reason="Corrected mutable aggregate observation fields.",
                    supersedes_import_id=original.import_id,
                    supersedes_observation_ids=tuple(
                        prior_event["observation_ids"]
                    ),
                    expected_analytical_identity_sha256=(
                        original.analytical_identity_sha256
                    ),
                )
                with (
                    patch(
                        "outcome_data_prep.workflow.require_approved_runtime"
                    ),
                    patch.object(
                        tier4_contracts,
                        "_authority_secret_fingerprint_for_registry",
                        return_value=AUTHORITY_SECRET_SHA256,
                    ),
                ):
                    corrected = import_results(request, correction)
                self.assertTrue(original.generation_path.is_dir())
                self.assertTrue(corrected.generation_path.is_dir())
                self.assertNotEqual(
                    original.import_digest, corrected.import_digest
                )
                self.assertEqual(
                    original.analytical_identity_sha256,
                    corrected.analytical_identity_sha256,
                )

    def test_correction_projection_separates_mutable_values_from_identity(self):
        original = self._run_meta_import(import_id="import-projection-contract")
        row = json.loads(
            (
                original.generation_path / "normalized-observations.json"
            ).read_text()
        )[0]
        expected = _correction_static_projection(row)

        mutable = deepcopy(row)
        mutable.update({
            "observation_id": "observation-replacement",
            "source_id": "replacement-source",
            "source_sha256": "sha256:" + "0" * 64,
            "source_row_reference": "replacement:99",
        })
        mutable["spend"].update({
            "value": 999,
            "decimal": "999.00",
            "source_numeric_text": "999.00",
        })
        mutable["exposure"]["impressions"].update({
            "value": 900,
            "source_numeric_text": "900",
        })
        mutable["exposure"]["clicks"].update({
            "value": 99,
            "source_numeric_text": "99",
        })
        mutable["outcome"].update({
            "value": 4,
            "decimal": "4",
            "source_numeric_text": "4",
            "value_state": "observed_zero",
        })
        mutable["validation_projection"].update({
            "aggregate": {
                "success_count": 4,
                "eligible_exposure_count": 900,
            },
            "eligible_exposure_count": 900,
            "missing_outcome_count": 2,
            "effective_sample_size": 898.0,
            "outcome_accessed_at": "2026-08-11T12:00:00Z",
        })
        self.assertEqual(expected, _correction_static_projection(mutable))

        immutable_changes = {
            "creative": lambda item: item["creative"].update(
                platform_id="different-creative"
            ),
            "campaign": lambda item: item["campaign"].update(
                platform_id="different-campaign"
            ),
            "metric": lambda item: item["outcome"].update(
                metric_id="different-metric"
            ),
            "denominator": lambda item: item[
                "validation_projection"
            ].update(aggregate={"event_count": 3, "exposure_time": 100}),
            "unit": lambda item: item["spend"].update(
                source_unit="different-unit"
            ),
            "currency": lambda item: item["currency"].update(code="EUR"),
            "timezone": lambda item: item["reporting"].update(timezone="UTC"),
            "attribution": lambda item: item["attribution"].update(
                report_time="impression"
            ),
            "reporting-basis": lambda item: item["reporting"].update(
                basis="different-basis"
            ),
            "measurement-window": lambda item: item[
                "validation_projection"
            ].update(measurement_window="different-window"),
        }
        for label, mutate in immutable_changes.items():
            with self.subTest(label=label):
                changed = deepcopy(row)
                mutate(changed)
                self.assertNotEqual(
                    expected, _correction_static_projection(changed)
                )

    def test_correction_supersession_ids_reject_duplicates_extra_and_missing(self):
        first = self._run_meta_import(import_id="import-id-set-original")
        prior_event = json.loads(
            (first.generation_path / "import-event.json").read_text()
        )
        prior_id = prior_event["observation_ids"][0]
        source, governance, context = self._meta_import_source(
            outcome="4", name="id-set-replacement.json"
        )
        sealed = SimpleNamespace(
            delivery_map_sha256=json.loads(
                (self.output / "delivery-map.json").read_text()
            )["delivery_map_sha256"]
        )
        cases = {
            "duplicate": (prior_id, prior_id),
            "extra": (prior_id, "observation-not-in-prior"),
            "missing": (),
        }
        for label, requested_ids in cases.items():
            with self.subTest(label=label):
                import_id = f"import-id-set-{label}"
                request = ImportRequest(
                    study_root=self.output,
                    sources=(self._source_input(
                        source, governance, context, sealed
                    ),),
                    authority_registry=(
                        self.root / "trusted-authority-registry.json"
                    ),
                    authority_secret_file=self.authority_secret_file,
                    imported_at="2026-08-10T12:02:00Z",
                    import_id=import_id,
                )
                correction = CorrectionInput(
                    correction_id=f"correction-id-set-{label}",
                    requested_at="2026-08-10T12:01:30Z",
                    actor="validation-owner",
                    reason_code="measurement_correction",
                    reason="Invalid supersession identity set.",
                    supersedes_import_id=first.import_id,
                    supersedes_observation_ids=requested_ids,
                    expected_analytical_identity_sha256=(
                        first.analytical_identity_sha256
                    ),
                )
                with (
                    patch(
                        "outcome_data_prep.workflow.require_approved_runtime"
                    ),
                    patch.object(
                        tier4_contracts,
                        "_authority_secret_fingerprint_for_registry",
                        return_value=AUTHORITY_SECRET_SHA256,
                    ),
                    self.assertRaisesRegex(
                        ImportConflict, "every authenticated prior"
                    ),
                ):
                    import_results(request, correction)
                self.assertFalse(
                    (self.output / "imports" / import_id).exists()
                )

    def test_correction_rejects_duplicate_replacement_immutable_identity(self):
        first = self._run_meta_import(import_id="import-duplicate-original")
        prior_event = json.loads(
            (first.generation_path / "import-event.json").read_text()
        )
        source, governance, context = self._meta_import_source(
            outcome="4", name="duplicate-replacement.json"
        )
        document = json.loads(source.read_text())
        duplicate = deepcopy(document["rows"][0])
        duplicate["actions"][1]["value"] = "5"
        document["rows"].append(duplicate)
        source.write_bytes(canonical_json_bytes(document))
        sealed = SimpleNamespace(
            delivery_map_sha256=json.loads(
                (self.output / "delivery-map.json").read_text()
            )["delivery_map_sha256"]
        )
        request = ImportRequest(
            study_root=self.output,
            sources=(self._source_input(
                source, governance, context, sealed
            ),),
            authority_registry=self.root / "trusted-authority-registry.json",
            authority_secret_file=self.authority_secret_file,
            imported_at="2026-08-10T12:02:00Z",
            import_id="import-duplicate-replacement",
        )
        correction = CorrectionInput(
            correction_id="correction-duplicate-replacement",
            requested_at="2026-08-10T12:01:30Z",
            actor="validation-owner",
            reason_code="measurement_correction",
            reason="Attempted duplicate replacement identity.",
            supersedes_import_id=first.import_id,
            supersedes_observation_ids=tuple(prior_event["observation_ids"]),
            expected_analytical_identity_sha256=(
                first.analytical_identity_sha256
            ),
        )
        with (
            patch("outcome_data_prep.workflow.require_approved_runtime"),
            patch.object(
                tier4_contracts,
                "_authority_secret_fingerprint_for_registry",
                return_value=AUTHORITY_SECRET_SHA256,
            ),
            self.assertRaisesRegex(ImportConflict, "analytical identity"),
        ):
            import_results(request, correction)
        self.assertTrue(first.generation_path.is_dir())
        self.assertFalse(
            (self.output / "imports" / request.import_id).exists()
        )

    def test_two_row_correction_must_name_every_prior_observation(self):
        first_plan = deepcopy(self.campaign_plan)
        first_plan.update({"platform": "meta_ads", "mapping_id": "map-one"})
        second_plan = deepcopy(first_plan)
        second_plan.update({
            "mapping_id": "map-two",
            "platform_campaign_id": "platform-campaign-2",
            "platform_ad_group_id": "platform-adset-2",
            "platform_ad_id": "platform-ad-456",
            "platform_creative_id": "platform-ad-456",
        })
        facts = deepcopy(self.supplied_facts)
        facts["delivery_start_evidence"]["evidence_source_sha256"] = (
            sha256_json(first_plan)
        )
        sealed = self._seal(self._draft(facts, [first_plan, second_plan]))
        source, governance, context = self._meta_import_source(
            name="two-row-original.json"
        )
        document = json.loads(source.read_text())
        second_row = deepcopy(document["rows"][0])
        second_row.update({
            "campaign_id": "platform-campaign-2",
            "adset_id": "platform-adset-2",
            "ad_id": "platform-ad-456",
        })
        document["rows"].append(second_row)
        source.write_bytes(canonical_json_bytes(document))
        original_request = ImportRequest(
            study_root=sealed.study_root,
            sources=(self._source_input(
                source, governance, context, sealed
            ),),
            authority_registry=self.root / "trusted-authority-registry.json",
            authority_secret_file=self.authority_secret_file,
            imported_at="2026-08-10T12:01:00Z",
            import_id="import-two-row-original",
        )
        with (
            patch("outcome_data_prep.workflow.require_approved_runtime"),
            patch.object(
                tier4_contracts,
                "_authority_secret_fingerprint_for_registry",
                return_value=AUTHORITY_SECRET_SHA256,
            ),
        ):
            original = import_results(original_request)
            before_state = validate_study(
                sealed.study_root,
                authority_registry=(
                    self.root / "trusted-authority-registry.json"
                ),
                authority_secret_file=self.authority_secret_file,
            )
        prior_event = json.loads(
            (original.generation_path / "import-event.json").read_text()
        )
        replacement = self.root / "two-row-replacement.json"
        replacement_document = deepcopy(document)
        for row in replacement_document["rows"]:
            row["actions"][1]["value"] = "4"
        replacement.write_bytes(canonical_json_bytes(replacement_document))
        replacement_request = ImportRequest(
            study_root=sealed.study_root,
            sources=(self._source_input(
                replacement, governance, context, sealed
            ),),
            authority_registry=self.root / "trusted-authority-registry.json",
            authority_secret_file=self.authority_secret_file,
            imported_at="2026-08-10T12:02:00Z",
            import_id="import-two-row-incomplete-correction",
        )
        correction = CorrectionInput(
            correction_id="correction-incomplete-two-row",
            requested_at="2026-08-10T12:01:30Z",
            actor="validation-owner",
            reason_code="measurement_correction",
            reason="Attempted partial correction.",
            supersedes_import_id=original.import_id,
            supersedes_observation_ids=(prior_event["observation_ids"][0],),
            expected_analytical_identity_sha256=(
                original.analytical_identity_sha256
            ),
        )
        with (
            patch("outcome_data_prep.workflow.require_approved_runtime"),
            patch.object(
                tier4_contracts,
                "_authority_secret_fingerprint_for_registry",
                return_value=AUTHORITY_SECRET_SHA256,
            ),
            self.assertRaisesRegex(ImportConflict, "every authenticated prior"),
        ):
            import_results(replacement_request, correction)
        self.assertTrue(original.generation_path.is_dir())
        self.assertFalse(
            (sealed.study_root / "imports" / replacement_request.import_id).exists()
        )
        with (
            patch("outcome_data_prep.workflow.require_approved_runtime"),
            patch.object(
                tier4_contracts,
                "_authority_secret_fingerprint_for_registry",
                return_value=AUTHORITY_SECRET_SHA256,
            ),
        ):
            after_state = validate_study(
                sealed.study_root,
                authority_registry=(
                    self.root / "trusted-authority-registry.json"
                ),
                authority_secret_file=self.authority_secret_file,
            )
        self.assertEqual(before_state, after_state)

        crossed = self.root / "two-row-crossed-identity.json"
        crossed_document = deepcopy(replacement_document)
        first_ad = crossed_document["rows"][0]["ad_id"]
        crossed_document["rows"][0]["ad_id"] = (
            crossed_document["rows"][1]["ad_id"]
        )
        crossed_document["rows"][1]["ad_id"] = first_ad
        crossed.write_bytes(canonical_json_bytes(crossed_document))
        crossed_request = ImportRequest(
            study_root=sealed.study_root,
            sources=(self._source_input(
                crossed, governance, context, sealed
            ),),
            authority_registry=self.root / "trusted-authority-registry.json",
            authority_secret_file=self.authority_secret_file,
            imported_at="2026-08-10T12:03:00Z",
            import_id="import-two-row-crossed-identity",
        )
        crossed_correction = CorrectionInput(
            correction_id="correction-crossed-two-row",
            requested_at="2026-08-10T12:02:30Z",
            actor="validation-owner",
            reason_code="measurement_correction",
            reason="Attempted crossed immutable identities.",
            supersedes_import_id=original.import_id,
            supersedes_observation_ids=tuple(prior_event["observation_ids"]),
            expected_analytical_identity_sha256=(
                original.analytical_identity_sha256
            ),
        )
        with (
            patch("outcome_data_prep.workflow.require_approved_runtime"),
            patch.object(
                tier4_contracts,
                "_authority_secret_fingerprint_for_registry",
                return_value=AUTHORITY_SECRET_SHA256,
            ),
            self.assertRaisesRegex(ImportConflict, "analytical identity"),
        ):
            import_results(crossed_request, crossed_correction)
        self.assertFalse(
            (sealed.study_root / "imports" / crossed_request.import_id).exists()
        )

    def test_correction_rejects_creative_metric_window_or_identity_change(self):
        first = self._run_meta_import(import_id="import-before-bad-correction")
        prior_event = json.loads(
            (first.generation_path / "import-event.json").read_text()
        )
        cases = (
            ("creative", {"ad_id": "different-ad"}, None),
            ("metric", {}, "different-metric"),
            ("window", {"report_date": "2026-08-11"}, None),
            ("analytical-identity", {}, "bad-expected-digest"),
        )
        for label, source_changes, special in cases:
            with self.subTest(label=label):
                source, governance, context = self._meta_import_source(
                    outcome="4",
                    name=f"meta-wrong-{label}.json",
                    **source_changes,
                )
                expected_identity = first.analytical_identity_sha256
                if special == "different-metric":
                    context["adapter_registration"]["metric_id"] = (
                        "different-metric"
                    )
                elif special == "bad-expected-digest":
                    expected_identity = "sha256:" + "0" * 64
                request = ImportRequest(
                    study_root=self.output,
                    sources=(self._source_input(
                        source, governance, context,
                        SimpleNamespace(
                            delivery_map_sha256=json.loads(
                                (self.output / "delivery-map.json").read_text()
                            )["delivery_map_sha256"]
                        ),
                    ),),
                    authority_registry=(
                        self.root / "trusted-authority-registry.json"
                    ),
                    authority_secret_file=self.authority_secret_file,
                    imported_at="2026-08-10T12:02:00Z",
                    import_id=f"import-bad-{label}",
                )
                correction = CorrectionInput(
                    correction_id=f"correction-bad-{label}",
                    requested_at="2026-08-10T12:01:30Z",
                    actor="validation-owner",
                    reason_code="measurement_correction",
                    reason="Attempted change outside observed values.",
                    supersedes_import_id=first.import_id,
                    supersedes_observation_ids=tuple(
                        prior_event["observation_ids"]
                    ),
                    expected_analytical_identity_sha256=expected_identity,
                )
                with (
                    patch(
                        "outcome_data_prep.workflow.require_approved_runtime"
                    ),
                    patch.object(
                        tier4_contracts,
                        "_authority_secret_fingerprint_for_registry",
                        return_value=AUTHORITY_SECRET_SHA256,
                    ),
                    self.assertRaisesRegex(
                        ImportConflict,
                        "creative, metric, window|analytical identity",
                    ),
                ):
                    import_results(request, correction)
                self.assertTrue(first.generation_path.exists())
                self.assertFalse(
                    (self.output / "imports" / request.import_id).exists()
                )

    def test_each_source_keeps_its_governance_and_context_by_exact_index(self):
        first_plan = deepcopy(self.campaign_plan)
        first_plan["platform"] = "meta_ads"
        first_plan["mapping_id"] = "mapping-meta-one"
        second_plan = deepcopy(first_plan)
        second_plan.update({
            "mapping_id": "mapping-meta-two",
            "platform_campaign_id": "platform-campaign-2",
            "platform_ad_group_id": "platform-adset-2",
            "platform_ad_id": "platform-ad-456",
            "platform_creative_id": "platform-ad-456",
        })
        facts = deepcopy(self.supplied_facts)
        facts["delivery_start_evidence"]["evidence_source_sha256"] = (
            sha256_json(first_plan)
        )
        sealed = self._seal(
            self._draft(facts, [first_plan, second_plan])
        )
        first = self._meta_import_source(
            name="meta-one.json",
            export_timestamp="2026-08-10T12:00:00Z",
            account_timezone="America/New_York",
        )
        second = self._meta_import_source(
            campaign_id="platform-campaign-2",
            adset_id="platform-adset-2",
            ad_id="platform-ad-456",
            name="meta-two.json",
            export_timestamp="2026-08-10T13:00:00Z",
            account_timezone="UTC",
        )
        request = ImportRequest(
            study_root=sealed.study_root,
            sources=(
                self._source_input(*first, sealed),
                self._source_input(*second, sealed),
            ),
            authority_registry=self.root / "trusted-authority-registry.json",
            authority_secret_file=self.authority_secret_file,
            imported_at="2026-08-10T13:01:00Z",
            import_id="import-two-sources",
        )
        with (
            patch("outcome_data_prep.workflow.require_approved_runtime"),
            patch.object(
                tier4_contracts,
                "_authority_secret_fingerprint_for_registry",
                return_value=AUTHORITY_SECRET_SHA256,
            ),
        ):
            result = import_results(request)
        records = json.loads(
            (result.generation_path / "source-governance-records.json").read_text()
        )
        by_name = {
            item["source_filename"]: item for item in records
        }
        self.assertEqual(
            "2026-08-10T12:00:00Z",
            by_name["meta-one.json"]["governance_input"]["export_timestamp"],
        )
        self.assertEqual(
            "2026-08-10T13:00:00Z",
            by_name["meta-two.json"]["governance_input"]["export_timestamp"],
        )
        rows = json.loads(
            (result.generation_path / "normalized-observations.json").read_text()
        )
        timezone_by_campaign = {
            row["campaign"]["platform_id"]: row["reporting"]["timezone"]
            for row in rows
        }
        self.assertEqual(
            "America/New_York",
            timezone_by_campaign["platform-campaign-1"],
        )
        self.assertEqual("UTC", timezone_by_campaign["platform-campaign-2"])

    def test_source_context_is_bound_to_exact_bytes_inventory_and_study(self):
        self.campaign_plan["platform"] = "meta_ads"
        facts = deepcopy(self.supplied_facts)
        facts["delivery_start_evidence"]["evidence_source_sha256"] = (
            sha256_json(self.campaign_plan)
        )
        sealed = self._seal(self._draft(facts))
        first, governance, raw_context = self._meta_import_source(
            outcome="3", name="same-schema-first.json"
        )
        second, _, _ = self._meta_import_source(
            outcome="4", name="same-schema-second.json"
        )
        bound = self._bound_context(
            source=first, context=raw_context, sealed=sealed
        )

        def request(
            import_id: str,
            source: Path,
            context: dict[str, object],
            inputs: tuple[SourceInput, ...] | None = None,
        ) -> ImportRequest:
            return ImportRequest(
                study_root=sealed.study_root,
                sources=inputs or (SourceInput(source, governance, context),),
                authority_registry=(
                    self.root / "trusted-authority-registry.json"
                ),
                authority_secret_file=self.authority_secret_file,
                imported_at="2026-08-10T12:01:00Z",
                import_id=import_id,
            )

        missing = deepcopy(bound)
        missing.pop("source_binding")
        wrong = deepcopy(bound)
        wrong["source_binding"]["source_sha256"] = "sha256:" + "0" * 64
        extra = deepcopy(bound)
        extra["unbound_hint"] = "must-not-be-accepted"
        replay = deepcopy(bound)
        replay["source_binding"]["delivery_map_sha256"] = (
            "sha256:" + "1" * 64
        )
        failures = (
            ("missing", request("binding-missing", first, missing)),
            ("wrong", request("binding-wrong", first, wrong)),
            ("extra", request("binding-extra", first, extra)),
            ("same-schema-swap", request("binding-swap", second, bound)),
            ("study-replay", request("binding-replay", first, replay)),
        )
        duplicate = self.root / "same-bytes-copy.json"
        duplicate.write_bytes(first.read_bytes())
        duplicate_context = self._bound_context(
            source=duplicate, context=raw_context, sealed=sealed
        )
        duplicate_context["reporting_metadata"]["account_timezone"] = "UTC"
        failures += ((
            "duplicate-conflicting-context",
            request(
                "binding-duplicate-conflict",
                first,
                bound,
                inputs=(
                    SourceInput(first, governance, bound),
                    SourceInput(duplicate, governance, duplicate_context),
                ),
            ),
        ),)
        changed_governance = deepcopy(governance)
        changed_governance["data_owner"] = "Different approved owner"
        changed_governance["source_governance_input_sha256"] = None
        changed_governance = validate_source_governance_input(
            changed_governance
        )
        failures += ((
            "duplicate-conflicting-governance",
            request(
                "binding-duplicate-governance",
                first,
                bound,
                inputs=(
                    SourceInput(first, governance, bound),
                    SourceInput(duplicate, changed_governance, bound),
                ),
            ),
        ),)
        with (
            patch("outcome_data_prep.workflow.require_approved_runtime"),
            patch.object(
                tier4_contracts,
                "_authority_secret_fingerprint_for_registry",
                return_value=AUTHORITY_SECRET_SHA256,
            ),
        ):
            for label, rejected in failures:
                with self.subTest(label=label), self.assertRaises(ContractError):
                    import_results(rejected)
        self.assertFalse((sealed.study_root / "imports").exists())

    def test_generic_csv_uses_explicit_profile_in_the_same_workflow(self):
        plan = deepcopy(self.campaign_plan)
        plan.update({
            "platform": "generic_dsp",
            "platform_campaign_id": "campaign-1",
            "platform_ad_group_id": "line-1",
            "platform_ad_id": "ad-1",
            "platform_creative_id": "creative-1",
        })
        facts = deepcopy(self.supplied_facts)
        facts["delivery_start_evidence"]["evidence_source_sha256"] = (
            sha256_json(plan)
        )
        sealed = self._seal(self._draft(facts, [plan]))
        source, governance, context = self._generic_import_source(
            delivery_map_sha256=sealed.delivery_map_sha256
        )

        def request(
            import_id: str,
            selected_source: Path = source,
            selected_context: dict[str, object] | None = context,
        ) -> ImportRequest:
            return ImportRequest(
                study_root=sealed.study_root,
                sources=(
                    (
                        SourceInput(selected_source, governance, None)
                        if selected_context is None else self._source_input(
                            selected_source,
                            governance,
                            selected_context,
                            sealed,
                        )
                    ),
                ),
                authority_registry=(
                    self.root / "trusted-authority-registry.json"
                ),
                authority_secret_file=self.authority_secret_file,
                imported_at="2026-08-10T12:01:00Z",
                import_id=import_id,
            )

        wrong_fingerprint = deepcopy(context)
        wrong_fingerprint["reporting_metadata"]["header_fingerprint"] = (
            "sha256:" + "f" * 64
        )
        alternate, _, _ = self._generic_import_source(
            delivery_map_sha256=sealed.delivery_map_sha256,
            name="generic-alternate.csv",
            alternate_headers=True,
        )
        replayed = deepcopy(context)
        replayed["delivery_map_sha256"] = "sha256:" + "0" * 64
        failures = (
            ("missing-profile", request("generic-missing", source, None)),
            (
                "header-fingerprint",
                request("generic-header", source, wrong_fingerprint),
            ),
            ("context-swap", request("generic-swap", alternate, context)),
            ("profile-replay", request("generic-replay", source, replayed)),
        )
        with (
            patch("outcome_data_prep.workflow.require_approved_runtime"),
            patch.object(
                tier4_contracts,
                "_authority_secret_fingerprint_for_registry",
                return_value=AUTHORITY_SECRET_SHA256,
            ),
        ):
            for label, rejected_request in failures:
                with self.subTest(label=label), self.assertRaises(
                    (ContractError, ValueError)
                ):
                    import_results(rejected_request)
            result = import_results(request("generic-success"))

        self.assertEqual("incomplete", result.operational_status)
        self.assertEqual(1, result.matched_row_count)
        self.assertTrue(result.validation_handoff_written)
        generation_text = "\n".join(
            path.read_text(encoding="utf-8")
            for path in result.generation_path.glob("*.json")
        )
        self.assertNotIn("adapter_validation_sha256", generation_text)
        self.assertNotIn("profile_sha256", generation_text)

    def test_validate_and_recover_authenticate_current_ledger(self):
        result = self._run_meta_import(import_id="import-for-replay")
        with (
            patch("outcome_data_prep.workflow.require_approved_runtime"),
            patch.object(
                tier4_contracts,
                "_authority_secret_fingerprint_for_registry",
                return_value=AUTHORITY_SECRET_SHA256,
            ),
        ):
            validated = validate_study(
                self.output,
                authority_registry=self.root / "trusted-authority-registry.json",
                authority_secret_file=self.authority_secret_file,
            )
            recovered = recover_study_from_paths(
                self.output,
                authority_registry=self.root / "trusted-authority-registry.json",
                authority_secret_file=self.authority_secret_file,
            )
        self.assertEqual(result.import_id, validated.current_import_id)
        self.assertEqual(validated, recovered)

    def test_import_cli_guard_and_exit_code_classes(self):
        script = PREP_SCRIPTS / "import-outcome-results.py"
        spec = importlib.util.spec_from_file_location("task11_import_cli", script)
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        base = [
            "--study-root", str(self.output),
            "--authority-registry", str(self.root / "registry.json"),
            "--authority-secret-file", str(self.authority_secret_file),
            "--imported-at", "2026-08-10T12:01:00Z",
            "--import-id", "import-cli",
        ]
        with (
            patch.object(module, "require_approved_runtime"),
            redirect_stderr(io.StringIO()),
        ):
            self.assertEqual(
                2,
                module.main(base + ["--source", str(self.root / "one.csv")]),
            )
        with (
            patch.object(
                module,
                "require_approved_runtime",
                side_effect=ContractError("runtime rejected"),
            ),
            patch.object(module, "_load_object") as load,
            patch.object(module, "import_results") as workflow,
            redirect_stderr(io.StringIO()),
        ):
            self.assertEqual(2, module.main(base))
            load.assert_not_called()
            workflow.assert_not_called()
        paired = base + [
            "--source", str(self.root / "one.csv"),
            "--source-governance", str(self.root / "governance.json"),
        ]
        with (
            patch.object(module, "require_approved_runtime"),
            patch.object(module, "_load_object", return_value={}),
            patch.object(
                module,
                "import_results",
                side_effect=__import__(
                    "outcome_data_prep.publication",
                    fromlist=["ImportConflict"],
                ).ImportConflict("collision"),
            ),
            redirect_stderr(io.StringIO()),
        ):
            self.assertEqual(3, module.main(paired))

    def test_import_cli_uses_canonical_valid_release_operation_first(self):
        script = PREP_SCRIPTS / "import-outcome-results.py"
        spec = importlib.util.spec_from_file_location(
            "task11_import_cli_release", script
        )
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)

        runtime = self.root / "deterministic-release"
        skill = runtime / "skills" / "real-world-outcome-data-prep"
        skill.mkdir(parents=True)
        release_file = skill / "SKILL.md"
        release_file.write_text("approved deterministic release\n")
        files = {
            release_file.relative_to(runtime).as_posix(): hashlib.sha256(
                release_file.read_bytes()
            ).hexdigest()
        }
        identity = {
            "schema_version": "outcome-prep-runtime-release-v2",
            "repository": "innovaitionpartners/audience-ad-testing-lab",
            "release_version": "task11-test",
            "files": files,
        }
        manifest = {
            **identity,
            "release_tree_sha256": sha256_bytes(
                canonical_json_bytes(identity)
            ),
        }
        events: list[object] = []

        def guard(operation: str):
            events.append(("guard", operation))
            return verify_runtime_identity(
                plugin_root=runtime,
                release_manifest=manifest,
                operation=operation,
            )

        def load(_path: Path, label: str):
            events.append(("read", label))
            return {}

        result = SimpleNamespace(
            import_id="import-cli-release",
            import_digest="sha256:" + "1" * 64,
            generation_path=self.root / "generation",
            ledger_digest="sha256:" + "2" * 64,
            analytical_identity_sha256="sha256:" + "3" * 64,
            evidence_status="preregistered_holdout",
            operational_status="incomplete",
            validation_handoff_written=True,
            source_count=1,
            matched_row_count=1,
            quarantined_row_count=0,
        )

        def workflow(*_args):
            events.append(("workflow", "import_results"))
            return result

        args = [
            "--study-root", str(self.output),
            "--source", str(self.root / "source.json"),
            "--source-governance", str(self.root / "governance.json"),
            "--source-context", str(self.root / "context.json"),
            "--authority-registry", str(self.root / "registry.json"),
            "--authority-secret-file", str(self.authority_secret_file),
            "--imported-at", "2026-08-10T12:01:00Z",
            "--import-id", "import-cli-release",
        ]
        stdout = SimpleNamespace(buffer=io.BytesIO())
        with (
            patch.object(module, "require_approved_runtime", side_effect=guard),
            patch.object(module, "_load_object", side_effect=load),
            patch.object(module, "import_results", side_effect=workflow),
            patch.object(module.sys, "stdout", stdout),
        ):
            self.assertEqual(0, module.main(args))
        self.assertEqual(
            [
                ("guard", "import_results"),
                ("read", "source governance"),
                ("read", "source context"),
                ("workflow", "import_results"),
            ],
            events,
        )
        self.assertTrue(stdout.buffer.getvalue())

        with self.assertRaisesRegex(RuntimeGuardError, "operation"):
            verify_runtime_identity(
                plugin_root=runtime,
                release_manifest=manifest,
                operation="import_results_cli",
            )

        guarded_operations: list[str] = []

        def reject_after_record(operation: str):
            guarded_operations.append(operation)
            raise ContractError("stop before reads")

        with patch(
            "outcome_data_prep.workflow.require_approved_runtime",
            side_effect=reject_after_record,
        ):
            with self.assertRaisesRegex(ContractError, "stop before reads"):
                validate_study(
                    self.output,
                    authority_registry=self.root / "registry.json",
                    authority_secret_file=self.authority_secret_file,
                )
            with self.assertRaisesRegex(ContractError, "stop before reads"):
                recover_study_from_paths(
                    self.output,
                    authority_registry=self.root / "registry.json",
                    authority_secret_file=self.authority_secret_file,
                )
        self.assertEqual(
            ["validate_study", "recover_study"], guarded_operations
        )

    def _guarded_cli_import(
        self,
        *,
        import_id: str,
        mutate_source: bool = False,
        mutate_study: bool = False,
    ) -> tuple[int, bytes, Path]:
        script = PREP_SCRIPTS / "import-outcome-results.py"
        spec = importlib.util.spec_from_file_location(
            f"task14_guarded_cli_{import_id.replace('-', '_')}", script
        )
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)

        self.campaign_plan["platform"] = "meta_ads"
        facts = deepcopy(self.supplied_facts)
        facts["delivery_start_evidence"]["evidence_source_sha256"] = (
            sha256_json(self.campaign_plan)
        )
        study_root = self.root / f"study-{import_id}"
        sealed = self._seal(self._draft(facts), output=study_root)
        source, governance, context = self._meta_import_source(
            name=f"{import_id}-meta.json"
        )
        bound = self._source_input(source, governance, context, sealed)
        governance_path = self.root / f"{import_id}-governance.json"
        context_path = self.root / f"{import_id}-context.json"
        governance_path.write_bytes(canonical_json_bytes(bound.governance_input))
        assert bound.adapter_context is not None
        context_path.write_bytes(canonical_json_bytes(bound.adapter_context))

        if mutate_source:
            source.write_bytes(source.read_bytes() + b"\nchanged-source-byte\n")
        if mutate_study:
            registration = sealed.study_root / "study-registration.json"
            changed = registration.read_bytes().replace(
                b'"study_id":"campaign-study"',
                b'"study_id":"changed-study"',
                1,
            )
            self.assertNotEqual(registration.read_bytes(), changed)
            registration.write_bytes(changed)

        args = [
            "--study-root", str(sealed.study_root),
            "--source", str(source),
            "--source-governance", str(governance_path),
            "--source-context", str(context_path),
            "--authority-registry", str(
                self.root / "trusted-authority-registry.json"
            ),
            "--authority-secret-file", str(self.authority_secret_file),
            "--imported-at", "2026-08-10T12:01:00Z",
            "--import-id", import_id,
        ]
        stdout = SimpleNamespace(buffer=io.BytesIO())
        with (
            patch.object(
                tier4_contracts,
                "_authority_secret_fingerprint_for_registry",
                return_value=AUTHORITY_SECRET_SHA256,
            ),
            patch.object(module.sys, "stdout", stdout),
            redirect_stderr(io.StringIO()),
        ):
            return module.main(args), stdout.buffer.getvalue(), sealed.study_root

    @unittest.skipUnless(
        RELEASE_MANIFEST.is_file(), "release manifest not generated yet"
    )
    def test_guarded_public_cli_observes_first_real_schema_tested_readiness(self):
        identity = require_approved_runtime("import_results")
        self.assertEqual(
            "innovaitionpartners/audience-ad-testing-lab",
            identity.repository,
        )
        return_code, stdout, study_root = self._guarded_cli_import(
            import_id="task14-acme-schema-tested"
        )
        self.assertEqual(0, return_code)
        response = json.loads(stdout)
        self.assertEqual("incomplete", response["operational_status"])
        self.assertTrue(response["validation_handoff_written"])

        generation = Path(response["generation_path"])
        self.assertTrue(generation.is_relative_to(study_root))
        readiness = json.loads(
            (generation / "readiness-report.json").read_text(encoding="utf-8")
        )
        self.assertEqual("schema_tested", readiness["adapter_maturity"])
        self.assertEqual("incomplete", readiness["operational_status"])
        self.assertTrue(
            any("export verification" in reason for reason in readiness["reasons"])
        )
        handoff = validate_validation_handoff_document(
            json.loads(
                (generation / "validation-handoff.json").read_text(
                    encoding="utf-8"
                )
            )
        )
        self.assertEqual(1, len(handoff["normalized_observations"]))
        self.assertEqual(1, len(handoff["validation_observations"]))
        self.assertTrue({
            "comparison",
            "observed_ordering",
            "claim_family",
            "evaluation",
            "calibration",
            "activation",
        }.isdisjoint(handoff))

    @unittest.skipUnless(
        RELEASE_MANIFEST.is_file(), "release manifest not generated yet"
    )
    def test_guarded_public_cli_rejects_changed_source_or_sealed_study(self):
        for label, options in (
            ("source", {"mutate_source": True}),
            ("study", {"mutate_study": True}),
        ):
            with self.subTest(label=label):
                return_code, stdout, study_root = self._guarded_cli_import(
                    import_id=f"task14-changed-{label}",
                    **options,
                )
                self.assertEqual(2, return_code)
                self.assertEqual(b"", stdout)
                self.assertFalse((study_root / "imports").exists())


if __name__ == "__main__":
    unittest.main()
