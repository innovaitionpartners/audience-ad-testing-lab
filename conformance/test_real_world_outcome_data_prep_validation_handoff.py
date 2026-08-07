from __future__ import annotations

from contextlib import nullcontext
from copy import deepcopy
from dataclasses import replace
import csv
from io import StringIO
import json
import sys
from pathlib import Path
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
PREP_SCRIPTS = ROOT / "skills" / "real-world-outcome-data-prep" / "scripts"
PANEL_SCRIPTS = ROOT / "skills" / "audience-panel-builder" / "scripts"
sys.path.insert(0, str(PREP_SCRIPTS))
sys.path.insert(0, str(PANEL_SCRIPTS))

from conformance import test_real_world_outcome_data_prep_adapters as adapter_tests
from conformance import test_real_world_outcome_data_prep_golden_paths as study_tests
from conformance.test_tier4_validation_contracts import AUTHORITY_SECRET
from conformance.test_tier4_synthetic_comparison import AuthenticatedFixture
from outcome_data_prep.common import ContractError, sha256_json
from outcome_data_prep.adapters.dv360 import DV360Adapter
from outcome_data_prep.adapters.google_ads import GoogleAdsAdapter
from outcome_data_prep.adapters.linkedin import LinkedInAdsAdapter
from outcome_data_prep.adapters.meta import MetaInsightsAdapter
from outcome_data_prep.adapters.tiktok import TikTokAdsAdapter
from outcome_data_prep.adapters.trade_desk import TradeDeskAdapter
from outcome_data_prep.adapters.xandr import XandrAdapter
import outcome_data_prep.adapters.dv360 as dv360_module
import outcome_data_prep.adapters.base as adapter_base_module
import outcome_data_prep.adapters.generic_programmatic as generic_module
import outcome_data_prep.adapters.google_ads as google_module
import outcome_data_prep.adapters.linkedin as linkedin_module
import outcome_data_prep.adapters.meta as meta_module
import outcome_data_prep.adapters.tiktok as tiktok_module
import outcome_data_prep.adapters.trade_desk as trade_desk_module
import outcome_data_prep.adapters.xandr as xandr_module
from outcome_data_prep.capabilities import load_capability_registry
from outcome_data_prep.contracts import (
    IMPORT_EVENT_VERSION,
    SOURCE_GOVERNANCE_INPUT_VERSION,
    SOURCE_MANIFEST_VERSION,
    validate_source_governance_input,
    validate_source_manifest,
    validate_normalized_observation,
)
from outcome_data_prep.matching import match_normalized_rows
import outcome_data_prep.matching as matching_module
from outcome_data_prep.container_safety import inspect_container
import outcome_data_prep.normalization as normalization_module
from outcome_data_prep.normalization import (
    AuthenticatedNormalizedBatch,
    EffectiveEvidenceStatusAuthority,
    NormalizedBatchError,
    authenticate_effective_evidence_status,
    authenticate_normalized_batch,
)
from outcome_data_prep.privacy import (
    admit_source,
    pre_scan_obvious_privacy,
)
from outcome_data_prep.source_snapshot import snapshot_source
from outcome_data_prep.study_authority import (
    IMPORT_EVENT_DOMAIN,
    AuthenticatedStudy,
    authority_hmac,
    import_event_authority_projection,
    verify_study_authority,
)
from outcome_data_prep.validation_handoff import (
    build_validation_observation,
    validate_validation_handoff,
)


class RealWorldOutcomeDataPrepValidationHandoffTests(unittest.TestCase):
    maxDiff = None

    @classmethod
    def setUpClass(cls) -> None:
        adapter_tests.ProgrammaticAdapterGuardTests.setUpClass()

    def setUp(self) -> None:
        self.study_fixture = study_tests.OutcomePrepStudyGoldenPaths(
            "test_seal_authenticates_exact_receipt_and_never_ingresses_secret"
        )
        self.study_fixture.setUp()
        self.addCleanup(self.study_fixture.doCleanups)
        self.study_fixture.campaign_plan.update({
            "platform": "generic_dsp",
            "platform_campaign_id": "campaign-1",
            "platform_ad_group_id": "line-1",
            "platform_ad_id": "ad-1",
            "platform_creative_id": "creative-1",
            "batch_id": "batch-1",
            "variant_id": "variant-1",
        })
        self.study_fixture.supplied_facts["delivery_start_evidence"][
            "evidence_source_sha256"
        ] = sha256_json(self.study_fixture.campaign_plan)
        sealed = self.study_fixture._seal(self.study_fixture._draft())
        self.study, self.authority = self.study_fixture._authenticate(
            sealed.study_root
        )

        self.adapter_fixture = adapter_tests.ProgrammaticAdapterGuardTests(
            "test_generic_mapping_is_closed_self_hashed_and_not_verified"
        )
        self.adapter_fixture.setUp()
        self.addCleanup(self.adapter_fixture.doCleanups)
        self.adapter = self.adapter_fixture.generic
        self.governance = validate_source_governance_input({
            "schema_version": SOURCE_GOVERNANCE_INPUT_VERSION,
            "data_owner": "Acme data owner",
            "system_of_record": "Acme ad platform",
            "permission_reference": "permission-one",
            "confirmer": "validation-owner",
            "allowed_purpose": "aggregate outcome validation",
            "retention_policy": "retain with authenticated study",
            "minimum_group_size_rule": "10",
            "restricted_fields_removed_attestation": True,
            "export_method": "aggregate platform export",
            "export_timestamp": "2026-08-10T12:00:00Z",
            "source_governance_input_sha256": None,
        })

    def _adapter_registration(
        self, payload: dict[str, object]
    ) -> dict[str, object]:
        metadata = payload["reporting_metadata"]
        assert isinstance(metadata, dict)
        return {
            "study_id": self.study.delivery_map["study_id"],
            "registration_id": self.study.registration["registration_id"],
            "metric_id": self.study.registration["primary_metric"]["name"],
            "registered_source_metric": metadata["conversion_metric"],
            "outcomes_accessed": True,
            "sealed_delivery_map": deepcopy(self.study.delivery_map),
            "approved_mapping": deepcopy(payload["mapping"]),
            "approved_mapping_profile_id": metadata["mapping_profile_id"],
            "approved_header_fingerprint": metadata["header_fingerprint"],
            "approved_source_container": metadata["source_container"],
            "time_basis": metadata["time_basis"],
            "currency": metadata["currency"],
            "attribution_semantics": metadata["attribution_semantics"],
            "attribution_windows": deepcopy(metadata["attribution_windows"]),
        }

    def _payload(self, family: str) -> dict[str, object]:
        payload = deepcopy(self.adapter_fixture.generic_fixture)
        row = payload["rows"][0]["values"]
        headers = payload["reporting_metadata"]["headers"]
        mapping = payload["mapping"]
        if family == "binary_proportion":
            row["Approved Purchase"] = "20"
            row["Delivered"] = "100"
        elif family == "continuous_mean":
            row["Approved Purchase"] = "1.25"
            row["Delivered"] = "100"
            row["Sample Count"] = "95"
            row["Standard Deviation"] = "0.5"
            headers.extend(["Sample Count", "Standard Deviation"])
            mapping["Sample Count"] = "sample_count"
            mapping["Standard Deviation"] = "standard_deviation"
        elif family == "event_rate":
            row["Approved Purchase"] = "20"
            row["Delivered"] = "100"
            row["Exposure Time"] = "400.5"
            headers.append("Exposure Time")
            mapping["Exposure Time"] = "exposure_time"
        else:
            raise AssertionError(family)
        payload["reporting_metadata"]["header_fingerprint"] = sha256_json(
            sorted(headers)
        )
        return payload

    def _admit(self, family: str = "binary_proportion") -> dict[str, object]:
        payload = self._payload(family)
        registration = self._adapter_registration(payload)
        snapshot, inventory = self.adapter_fixture.generic_source_snapshot(
            payload, "tsv"
        )
        profile = self.adapter.approved_profile(
            payload,
            registration=registration,
            capability=self.adapter.capability,
        )
        adapter_inventory = self.adapter.inventory(
            inventory, self.adapter.capability, profile=profile
        )
        validation = self.adapter.validate(
            adapter_inventory,
            registration=registration,
            governance=self.governance,
            capability=self.adapter.capability,
        )
        admission = self.adapter.admission_validation(
            inventory,
            source_sha256=snapshot.source_sha256,
            validation=validation,
            registration=registration,
            governance=self.governance,
            profile=profile,
        )
        admitted = admit_source(
            snapshot,
            inventory,
            pre_scan_obvious_privacy(inventory),
            admission,
            self.adapter_fixture.generic_base
            / f"task9-{self.adapter_fixture.generic_source_index}.tsv",
        )
        source_id = "source-generic-1"
        manifest = validate_source_manifest({
            "schema_version": SOURCE_MANIFEST_VERSION,
            "source_manifest_id": (
                f"manifest-{self.adapter_fixture.generic_source_index}"
            ),
            "study_id": self.study.delivery_map["study_id"],
            "import_id": f"import-{self.adapter_fixture.generic_source_index}",
            "sources": [{
                "source_id": source_id,
                "source_sha256": admitted.source_sha256,
                "admission_sha256": admitted.admission_sha256,
            }],
            "source_manifest_sha256": None,
        })
        table = inventory.tables[0]
        row_number = min(
            cell.row_number for cell in inventory.cells if cell.table == table
        )
        metric_id = self.study.registration["primary_metric"]["name"]
        observation_id = "observation-" + sha256_json({
            "adapter_id": self.adapter.capability.adapter_id,
            "source_sha256": admitted.source_sha256,
            "source_row_reference": f"{table}:{row_number}",
            "metric_id": metric_id,
        }).removeprefix("sha256:")
        event = {
            "schema_version": IMPORT_EVENT_VERSION,
            "import_id": manifest["import_id"],
            "study_id": manifest["study_id"],
            "imported_at": "2026-08-10T12:01:00Z",
            "imported_by": "validation-owner",
            "source_manifest_sha256": manifest["source_manifest_sha256"],
            "observation_ids": [observation_id],
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
                        self.study.registration["registration_id"]
                    ),
                    receipt_sha256=str(
                        self.study.registration_receipt["receipt_sha256"]
                    ),
                ),
                secret=AUTHORITY_SECRET,
            ),
        }
        return {
            "inventory": inventory,
            "admission": admission,
            "admitted": admitted,
            "profile": profile,
            "manifest": manifest,
            "envelope": envelope,
            "observation_id": observation_id,
        }

    def _batch(
        self, family: str = "binary_proportion"
    ) -> tuple[AuthenticatedNormalizedBatch, dict[str, object]]:
        inputs = self._admit(family)
        batch = authenticate_normalized_batch(
            authenticated_study=self.study,
            study_authority=self.authority,
            source_inventory=inputs["inventory"],
            admission_validation=inputs["admission"],
            admitted_source=inputs["admitted"],
            governance_input=self.governance,
            profile=inputs["profile"],
            source_manifest=inputs["manifest"],
            import_event_envelope=inputs["envelope"],
        )
        return batch, inputs

    def _seal_named_study(
        self,
        *,
        platform: str,
        campaign_id: str,
        ad_group_id: str,
        ad_id: str,
        creative_id: str,
    ):
        fixture = study_tests.OutcomePrepStudyGoldenPaths(
            "test_seal_authenticates_exact_receipt_and_never_ingresses_secret"
        )
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        fixture.campaign_plan.update({
            "platform": platform,
            "platform_campaign_id": campaign_id,
            "platform_ad_group_id": ad_group_id,
            "platform_ad_id": ad_id,
            "platform_creative_id": creative_id,
            "batch_id": "batch-1",
            "variant_id": "variant-1",
        })
        fixture.supplied_facts["delivery_start_evidence"][
            "evidence_source_sha256"
        ] = sha256_json(fixture.campaign_plan)
        sealed = fixture._seal(fixture._draft())
        return fixture._authenticate(sealed.study_root)

    @staticmethod
    def _physical_exact_row(
        capability,
        logical_row: dict[str, object],
    ) -> dict[str, object]:
        if capability.platform == "google_ads":
            def dotted(path: str):
                value = logical_row
                for part in path.split("."):
                    value = value[part]  # type: ignore[index,assignment]
                return value

            return {
                field: dotted(field)
                for field in (
                    capability.identity_fields
                    + capability.required_fields
                    + capability.metric_fields
                )
            }
        if capability.platform == "linkedin_ads":
            pivots = logical_row["pivotValues"]
            dates = logical_row["dateRange"]
            assert isinstance(pivots, list) and isinstance(dates, dict)
            physical = {
                "account": pivots[0],
                "campaign": pivots[1],
                "creative": pivots[2],
                "dateRange.start": dates["start"],
                "dateRange.end": dates["end"],
            }
            for field in capability.required_fields + capability.metric_fields:
                physical[field] = logical_row[field]
            return physical
        return {
            field: logical_row[field]
            for field in (
                capability.identity_fields
                + capability.required_fields
                + capability.metric_fields
            )
        }

    def _write_exact_source(self, capability, physical: dict[str, object]):
        index = self.adapter_fixture.generic_source_index + 1
        self.adapter_fixture.generic_source_index = index
        suffix = "json" if capability.container == "json" else capability.container
        path = self.adapter_fixture.generic_base / f"exact-{index}.{suffix}"
        headers = list(
            capability.identity_fields
            + capability.required_fields
            + capability.metric_fields
        )
        if capability.container == "json":
            path.write_text(
                json.dumps({"rows": [physical]}, separators=(",", ":")),
                encoding="utf-8",
            )
        else:
            output = StringIO(newline="")
            writer = csv.writer(
                output,
                delimiter="\t" if capability.container == "tsv" else ",",
                lineterminator="\n",
            )
            writer.writerow(headers)
            writer.writerow([physical[field] for field in headers])
            path.write_text(output.getvalue(), encoding="utf-8", newline="")
        snapshot = snapshot_source(
            path,
            staging_root=self.adapter_fixture.generic_base / f"stage-{index}",
        )
        return snapshot, inspect_container(snapshot)

    def _exact_case(
        self,
        name: str,
        *,
        mapping_override: tuple[str, str, str, str, str] | None = None,
        authentication_context=None,
    ):
        if name in {"meta", "google"}:
            adapter_tests.MetaAndGoogleAdapterTests.setUpClass()
            fixture = adapter_tests.MetaAndGoogleAdapterTests("run")
            fixture.setUp()
        elif name in {"linkedin", "tiktok"}:
            adapter_tests.LinkedInAndTikTokAdapterTests.setUpClass()
            fixture = adapter_tests.LinkedInAndTikTokAdapterTests("run")
            fixture.setUp()
        else:
            fixture = adapter_tests.ProgrammaticAdapterGuardTests("run")
            fixture.setUp()
            self.addCleanup(fixture.doCleanups)
        adapters = {
            "meta": (
                MetaInsightsAdapter, "meta_capability", "meta_fixture",
                "meta_registration",
            ),
            "google": (
                GoogleAdsAdapter, "google_capability", "google_fixture",
                "google_registration",
            ),
            "linkedin": (
                LinkedInAdsAdapter, "linkedin_capability", "linkedin_fixture",
                "linkedin_registration",
            ),
            "tiktok": (
                TikTokAdsAdapter, "tiktok_capability", "tiktok_fixture",
                "tiktok_registration",
            ),
            "dv360": (
                DV360Adapter, "dv360_capability", "dv360_fixture",
                "registration",
            ),
            "ttd": (
                TradeDeskAdapter, "ttd_capability", "ttd_fixture",
                "ttd_registration",
            ),
            "xandr": (
                XandrAdapter, "xandr_capability", "xandr_fixture",
                "xandr_registration",
            ),
        }
        adapter_type, capability_name, payload_name, registration_name = adapters[name]
        capability = getattr(fixture, capability_name)
        adapter = adapter_type(capability)
        payload = deepcopy(getattr(fixture, payload_name))
        registration = deepcopy(getattr(fixture, registration_name))
        row = payload["rows"][0]
        if name == "meta":
            row["actions"][1]["value"] = "20"
        elif name == "google":
            row["metrics"]["conversions"] = "20"
        elif name == "linkedin":
            row["externalWebsiteConversions"] = "20"
        elif name == "tiktok":
            registration["registered_click_metric"] = "clicks"
            row["conversion"] = "20"
        elif name == "dv360":
            row["Total Conversions"] = "20"
        elif name == "ttd":
            row["Conversions"] = "20"
        elif name == "xandr":
            row["creative_id"] = "creative-1"
            row["post_click_convs"] = "20"
        preview = adapter.normalize(
            payload,
            registration=registration,
            capability=capability,
        ).normalized_rows[0]
        ids = [
            preview[field]["platform_id"]
            for field in ("campaign", "ad_group", "ad", "creative")
        ]
        mapped = mapping_override or (
            capability.platform, ids[0], ids[1], ids[2], ids[3]
        )
        study, authority = self._seal_named_study(
            platform=mapped[0],
            campaign_id=mapped[1],
            ad_group_id=mapped[2],
            ad_id=mapped[3],
            creative_id=mapped[4],
        )
        registration.update({
            "study_id": study.delivery_map["study_id"],
            "registration_id": study.registration["registration_id"],
            "metric_id": study.registration["primary_metric"]["name"],
        })
        physical = self._physical_exact_row(capability, row)
        snapshot, inventory = self._write_exact_source(capability, physical)
        context = {
            "adapter_registration": registration,
            "reporting_metadata": payload["reporting_metadata"],
        }
        adapter_inventory = adapter.inventory(inventory, capability)
        validation = adapter.validate(
            adapter_inventory,
            registration=registration,
            governance=self.governance,
            capability=capability,
        )
        admission = adapter.admission_validation(
            inventory,
            source_sha256=snapshot.source_sha256,
            validation=validation,
            registration=registration,
            governance=self.governance,
            normalization_context=context,
        )
        admitted = admit_source(
            snapshot,
            inventory,
            pre_scan_obvious_privacy(inventory),
            admission,
            self.adapter_fixture.generic_base
            / (
                f"admitted-{name}-"
                f"{self.adapter_fixture.generic_source_index}."
                f"{capability.container}"
            ),
        )
        source_id = f"source-{name}-1"
        import_id = f"import-{name}-1"
        manifest = validate_source_manifest({
            "schema_version": SOURCE_MANIFEST_VERSION,
            "source_manifest_id": f"manifest-{name}-1",
            "study_id": study.delivery_map["study_id"],
            "import_id": import_id,
            "sources": [{
                "source_id": source_id,
                "source_sha256": admitted.source_sha256,
                "admission_sha256": admitted.admission_sha256,
            }],
            "source_manifest_sha256": None,
        })
        table = inventory.tables[0]
        row_number = min(cell.row_number for cell in inventory.cells)
        payload.update({
            "source_id": source_id,
            "import_id": import_id,
            "source_sha256": admitted.source_sha256,
        })
        payload["rows"][0]["source_row_reference"] = f"{table}:{row_number}"
        preview = adapter.normalize(
            payload,
            registration=registration,
            capability=capability,
        ).normalized_rows[0]
        observation_id = preview["observation_id"]
        event = {
            "schema_version": IMPORT_EVENT_VERSION,
            "import_id": import_id,
            "study_id": manifest["study_id"],
            "imported_at": "2026-08-10T12:01:00Z",
            "imported_by": "validation-owner",
            "source_manifest_sha256": manifest["source_manifest_sha256"],
            "observation_ids": [observation_id],
            "import_event_sha256": None,
        }
        event["import_event_sha256"] = sha256_json(event)
        envelope = {
            "event": event,
            "event_hmac_sha256": authority_hmac(
                domain=IMPORT_EVENT_DOMAIN,
                payload=import_event_authority_projection(
                    event,
                    registration_id=str(study.registration["registration_id"]),
                    receipt_sha256=str(study.registration_receipt["receipt_sha256"]),
                ),
                secret=AUTHORITY_SECRET,
            ),
        }
        auth_context = (
            nullcontext()
            if authentication_context is None
            else authentication_context()
        )
        with auth_context:
            batch = authenticate_normalized_batch(
                authenticated_study=study,
                study_authority=authority,
                source_inventory=inventory,
                admission_validation=admission,
                admitted_source=admitted,
                governance_input=self.governance,
                adapter_context=context,
                source_manifest=manifest,
                import_event_envelope=envelope,
            )
        return study, authority, batch, observation_id, capability

    def test_actual_adapter_preserves_distinct_ad_and_creative_identity(self):
        batch, _ = self._batch()
        matched = match_normalized_rows(
            authenticated_batch=batch,
            authenticated_study=self.study,
            study_authority=self.authority,
        )
        self.assertEqual(1, len(matched.matched))
        row = matched.matched[0]["normalized_observation"]
        self.assertEqual("ad-1", row["ad"]["platform_id"])
        self.assertEqual("creative-1", row["creative"]["platform_id"])
        self.assertNotEqual("row-2", row["source_row_reference"])
        self.assertTrue(row["source_row_reference"].endswith(":2"))
        self.assertEqual(
            self.study.delivery_map["mappings"][0]["platform_ad_id"],
            row["ad"]["platform_id"],
        )

    def test_every_named_adapter_traverses_admitted_bytes_to_tier4(self):
        expected_platforms = {
            "meta": "meta_ads",
            "google": "google_ads",
            "linkedin": "linkedin_ads",
            "tiktok": "tiktok_ads",
            "dv360": "dv360",
            "ttd": "the_trade_desk",
            "xandr": "xandr",
        }
        for name, platform in expected_platforms.items():
            with self.subTest(adapter=name):
                study, authority, batch, observation_id, capability = (
                    self._exact_case(name)
                )
                matched = match_normalized_rows(
                    authenticated_batch=batch,
                    authenticated_study=study,
                    study_authority=authority,
                )
                self.assertEqual(1, len(matched.matched))
                normalized = matched.matched[0]["normalized_observation"]
                self.assertEqual(platform, normalized["platform"])
                self.assertEqual(
                    capability.adapter_id,
                    normalized["adapter"]["adapter_id"],
                )
                observation = build_validation_observation(
                    observation_id=observation_id,
                    authenticated_batch=batch,
                    authenticated_study=study,
                    study_authority=authority,
                )
                self.assertEqual("binary_proportion", observation["metric_family"])
                self.assertEqual(20, observation["aggregate"]["success_count"])

    def test_every_registered_variant_has_closed_handoff_disposition(self):
        registry = load_capability_registry(
            ROOT
            / "skills"
            / "real-world-outcome-data-prep"
            / "references"
            / "platform-capabilities.json"
        )
        admitted = self._admit()["admission"]
        supported = set(normalization_module._IMPLEMENTATIONS_BY_ID)
        for capability in registry:
            probe = replace(
                admitted,
                adapter_id=capability.adapter_id,
                adapter_version=capability.adapter_version,
            )
            with self.subTest(adapter_id=capability.adapter_id):
                if capability.adapter_id in supported:
                    implementation, resolved, adapter = (
                        normalization_module._resolve_implementation(probe)
                    )
                    self.assertEqual(capability, resolved)
                    self.assertIs(type(adapter), implementation.adapter_type)
                else:
                    message = (
                        capability.availability_reason
                        if capability.maturity == "blocked"
                        else "no semantic implementation"
                    )
                    with self.assertRaisesRegex(NormalizedBatchError, message):
                        normalization_module._resolve_implementation(probe)

    def test_all_three_metric_families_derive_from_admitted_bytes(self):
        expected = {
            "binary_proportion": {
                "success_count": 20,
                "eligible_exposure_count": 100,
            },
            "continuous_mean": {
                "sample_count": 95,
                "mean": 1.25,
                "standard_deviation": 0.5,
            },
            "event_rate": {"event_count": 20, "exposure_time": 400.5},
        }
        for family, aggregate in expected.items():
            with self.subTest(family=family):
                batch, inputs = self._batch(family)
                observation = build_validation_observation(
                    observation_id=inputs["observation_id"],
                    authenticated_batch=batch,
                    authenticated_study=self.study,
                    study_authority=self.authority,
                )
                self.assertEqual(family, observation["metric_family"])
                self.assertEqual(aggregate, observation["aggregate"])

    def test_authenticated_prior_result_access_materializes_leaked_status(self):
        fixture = study_tests.OutcomePrepStudyGoldenPaths("run")
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        fixture.campaign_plan.update({
            "platform": "generic_dsp",
            "platform_campaign_id": "campaign-1",
            "platform_ad_group_id": "line-1",
            "platform_ad_id": "ad-1",
            "platform_creative_id": "creative-1",
            "batch_id": "batch-1",
            "variant_id": "variant-1",
        })
        fixture.supplied_facts["delivery_start_evidence"][
            "evidence_source_sha256"
        ] = sha256_json(fixture.campaign_plan)
        initial = fixture._draft()
        template = deepcopy(initial.preregistration)
        result_sha = template["synthetic_surface"]["result_sha256"]
        template["prior_outcome_access"] = [{
            "access_sha256": result_sha,
            "accessed_at": "2026-07-30T13:00:00Z",
            "kind": "authorized-outcome-review",
        }]
        facts = deepcopy(fixture.supplied_facts)
        facts["preregistration_template"] = template
        leaked = fixture._seal(fixture._draft(facts))
        self.study, self.authority = fixture._authenticate(leaked.study_root)
        batch, inputs = self._batch()
        observation = build_validation_observation(
            observation_id=inputs["observation_id"],
            authenticated_batch=batch,
            authenticated_study=self.study,
            study_authority=self.authority,
        )
        self.assertTrue(observation["assignment"]["leakage_detected"])
        self.assertEqual("leaked", observation["holdout_status"])

    def test_authenticated_nonrandomized_design_retains_mismatched_status(self):
        fixture = study_tests.OutcomePrepStudyGoldenPaths("run")
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        fixture.campaign_plan.update({
            "platform": "generic_dsp",
            "platform_campaign_id": "campaign-1",
            "platform_ad_group_id": "line-1",
            "platform_ad_id": "ad-1",
            "platform_creative_id": "creative-1",
            "batch_id": "batch-1",
            "variant_id": "variant-1",
        })
        fixture.supplied_facts["delivery_start_evidence"][
            "evidence_source_sha256"
        ] = sha256_json(fixture.campaign_plan)
        template = deepcopy(fixture._draft().preregistration)
        template["study_design_power"]["method"] = "observational-design-v1"
        facts = deepcopy(fixture.supplied_facts)
        facts["preregistration_template"] = template
        sealed = fixture._seal(fixture._draft(facts))
        self.study, self.authority = fixture._authenticate(sealed.study_root)
        batch, inputs = self._batch()
        observation = build_validation_observation(
            observation_id=inputs["observation_id"],
            authenticated_batch=batch,
            authenticated_study=self.study,
            study_authority=self.authority,
        )
        self.assertEqual("observational", observation["assignment"]["design"])
        self.assertEqual("mismatched", observation["holdout_status"])

    def test_source_impressions_cannot_be_promoted_by_resealed_row(self):
        batch, inputs = self._batch()
        matched = match_normalized_rows(
            authenticated_batch=batch,
            authenticated_study=self.study,
            study_authority=self.authority,
        )
        row = deepcopy(matched.matched[0]["normalized_observation"])
        row["exposure"]["impressions"]["value"] = 1_000_000
        row["validation_projection"]["eligible_exposure_count"] = 1_000_000
        row["normalized_observation_sha256"] = None
        row["normalized_observation_sha256"] = sha256_json(row)
        with self.assertRaises((TypeError, ContractError)):
            build_validation_observation(
                normalized_observation=row,
                registration=self.study.registration,
                delivery_binding=matched.matched[0]["delivery_binding"],
            )
        observation = build_validation_observation(
            observation_id=inputs["observation_id"],
            authenticated_batch=batch,
            authenticated_study=self.study,
            study_authority=self.authority,
        )
        self.assertEqual(100, observation["sample"]["eligible_exposure_count"])

    def test_effective_status_cannot_be_caller_chosen_or_fabricated(self):
        inputs = self._admit()
        with self.assertRaisesRegex(
            NormalizedBatchError, "only be minted from authenticated ledger"
        ):
            EffectiveEvidenceStatusAuthority()
        with self.assertRaises(TypeError):
            authenticate_normalized_batch(
                authenticated_study=self.study,
                study_authority=self.authority,
                source_inventory=inputs["inventory"],
                admission_validation=inputs["admission"],
                admitted_source=inputs["admitted"],
                governance_input=self.governance,
                profile=inputs["profile"],
                source_manifest=inputs["manifest"],
                import_event_envelope=inputs["envelope"],
                effective_evidence_status="blocked",
            )
        with self.assertRaisesRegex(
            NormalizedBatchError, "effective evidence status authority"
        ):
            authenticate_normalized_batch(
                authenticated_study=self.study,
                study_authority=self.authority,
                source_inventory=inputs["inventory"],
                admission_validation=inputs["admission"],
                admitted_source=inputs["admitted"],
                governance_input=self.governance,
                profile=inputs["profile"],
                source_manifest=inputs["manifest"],
                import_event_envelope=inputs["envelope"],
                effective_status_authority="blocked",
            )

    def test_status_record_constructors_require_exact_bound_authority(self):
        capability = authenticate_effective_evidence_status(
            authenticated_study=self.study,
            study_authority=self.authority,
        )
        fabricated = object.__new__(EffectiveEvidenceStatusAuthority)
        same_study, second_authority = self.study_fixture._authenticate(
            self.study.study_root
        )
        cross_study, cross_authority = self._seal_named_study(
            platform="generic_dsp",
            campaign_id="cross-campaign",
            ad_group_id="cross-line",
            ad_id="cross-ad",
            creative_id="cross-creative",
        )

        constructors = {
            "assignment": lambda cap, study, authority: (
                normalization_module._assignment_projection(
                    authenticated_study=study,
                    study_authority=authority,
                    effective_status_authority=cap,
                )
            ),
            "projection": lambda cap, study, authority: (
                normalization_module._projection(
                    row={},
                    values=None,
                    mapping=None,
                    authenticated_study=study,
                    study_authority=authority,
                    effective_status_authority=cap,
                    governance={},
                )
            ),
            "binding": lambda cap, study, authority: matching_module._binding(
                row={},
                mapping={},
                delivery_map_sha256="sha256:" + "0" * 64,
                authenticated_study=study,
                study_authority=authority,
                effective_status_authority=cap,
            ),
        }
        attacks = {
            "raw-string": ("blocked", self.study, self.authority),
            "object-new": (fabricated, self.study, self.authority),
            "cross-study": (capability, cross_study, cross_authority),
            "cross-authority": (capability, same_study, second_authority),
        }
        for constructor_name, constructor in constructors.items():
            for attack_name, arguments in attacks.items():
                with self.subTest(
                    constructor=constructor_name, attack=attack_name
                ):
                    with self.assertRaisesRegex(
                        NormalizedBatchError,
                        "effective evidence status authority",
                    ):
                        constructor(*arguments)

        with self.assertRaises(TypeError):
            normalization_module._assignment_projection(
                self.study, effective_evidence_status="blocked"
            )
        with self.assertRaises(TypeError):
            normalization_module._projection(
                row={},
                values=None,
                mapping=None,
                study=self.study,
                effective_evidence_status="blocked",
                governance={},
            )
        with self.assertRaises(TypeError):
            matching_module._binding(
                row={},
                mapping={},
                registration=self.study.registration,
                delivery_map_sha256="sha256:" + "0" * 64,
                evidence_status="blocked",
            )

    def test_batch_accepts_no_caller_adapter_or_fabricated_result_lane(self):
        inputs = self._admit()
        with self.assertRaises(TypeError):
            authenticate_normalized_batch(
                authenticated_study=self.study,
                study_authority=self.authority,
                adapter=self.adapter,
                source_inventory=inputs["inventory"],
                admission_validation=inputs["admission"],
                admitted_source=inputs["admitted"],
                governance_input=self.governance,
                profile=inputs["profile"],
                source_manifest=inputs["manifest"],
                import_event_envelope=inputs["envelope"],
            )

        original = type(self.adapter).normalize

        def fabricated(*args, **kwargs):
            result = original(*args, **kwargs)
            rows = list(result.normalized_rows)
            rows[0]["outcome"]["value"] = 99
            return replace(result, normalized_rows=tuple(rows))

        with patch.object(type(self.adapter), "normalize", fabricated):
            with self.assertRaisesRegex(
                NormalizedBatchError, "implementation was replaced"
            ):
                authenticate_normalized_batch(
                    authenticated_study=self.study,
                    study_authority=self.authority,
                    source_inventory=inputs["inventory"],
                    admission_validation=inputs["admission"],
                    admitted_source=inputs["admitted"],
                    governance_input=self.governance,
                    profile=inputs["profile"],
                    source_manifest=inputs["manifest"],
                    import_event_envelope=inputs["envelope"],
                )

        with (
            patch.object(normalization_module, "_IMPLEMENTATIONS_BY_ID", {}),
            patch.object(normalization_module, "_CAPABILITIES_BY_ID", {}),
        ):
            closed_batch = authenticate_normalized_batch(
                authenticated_study=self.study,
                study_authority=self.authority,
                source_inventory=inputs["inventory"],
                admission_validation=inputs["admission"],
                admitted_source=inputs["admitted"],
                governance_input=self.governance,
                profile=inputs["profile"],
                source_manifest=inputs["manifest"],
                import_event_envelope=inputs["envelope"],
            )
            self.assertIsInstance(closed_batch, AuthenticatedNormalizedBatch)

        observation = build_validation_observation(
            observation_id=inputs["observation_id"],
            authenticated_batch=authenticate_normalized_batch(
                authenticated_study=self.study,
                study_authority=self.authority,
                source_inventory=inputs["inventory"],
                admission_validation=inputs["admission"],
                admitted_source=inputs["admitted"],
                governance_input=self.governance,
                profile=inputs["profile"],
                source_manifest=inputs["manifest"],
                import_event_envelope=inputs["envelope"],
            ),
            authenticated_study=self.study,
            study_authority=self.authority,
        )
        self.assertEqual(20, observation["aggregate"]["success_count"])

    def test_generic_row_builder_cannot_replace_physical_outcome(self):
        """A helper-produced 99 cannot replace the admitted physical 20."""

        inputs = self._admit()
        original = generic_module.build_rich_observation

        def replace_outcome(*args, **kwargs):
            row = original(*args, **kwargs)
            changed = deepcopy(row)
            changed["outcome"].update({
                "value": 99,
                "decimal": "99",
                "source_numeric_text": "99",
                "value_state": "observed",
            })
            changed["normalized_observation_sha256"] = None
            return validate_normalized_observation(changed)

        with patch.object(
            generic_module, "build_rich_observation", replace_outcome
        ):
            with self.assertRaisesRegex(
                NormalizedBatchError, "generic normalized outcome is not physical"
            ):
                authenticate_normalized_batch(
                    authenticated_study=self.study,
                    study_authority=self.authority,
                    source_inventory=inputs["inventory"],
                    admission_validation=inputs["admission"],
                    admitted_source=inputs["admitted"],
                    governance_input=self.governance,
                    profile=inputs["profile"],
                    source_manifest=inputs["manifest"],
                    import_event_envelope=inputs["envelope"],
                )

    def test_named_adapter_semantic_helpers_cannot_rewrite_platform_facts(self):
        """Every named lane is checked against independent closed semantics."""

        modules = {
            "meta": (meta_module, "standard", "non_skan"),
            "google": (google_module, "standard", "non_skan"),
            "linkedin": (linkedin_module, "standard", "non_skan"),
            "tiktok": (tiktok_module, "delayed", "skan_delayed"),
            "dv360": (dv360_module, "standard", "non_skan"),
            "ttd": (trade_desk_module, "delayed", "non_skan"),
            "xandr": (xandr_module, "delayed", "non_skan"),
        }
        for name, (module, delivery_state, skan_state) in modules.items():
            original = module.build_platform_semantics

            def replace_semantics(
                *args,
                _original=original,
                _delivery=delivery_state,
                _skan=skan_state,
                **kwargs,
            ):
                semantics = _original(*args, **kwargs)
                semantics["delivery_state"] = _delivery
                semantics["skan_state"] = _skan
                return semantics

            with self.subTest(adapter=name):
                with patch.object(
                    module, "build_platform_semantics", replace_semantics
                ):
                    with self.assertRaisesRegex(
                        NormalizedBatchError,
                        "adapter platform fact is not admitted",
                    ):
                        self._exact_case(name)

    def test_meta_helper_cannot_mutate_trusted_timezone(self):
        original = meta_module.require_closed_object

        def mutate_timezone(value, keys, path, **kwargs):
            if path == "Meta reporting_metadata":
                value["account_timezone"] = "Forged/Zone"
            return original(value, keys, path, **kwargs)

        def auth_context():
            return patch.object(
                meta_module, "require_closed_object", mutate_timezone
            )

        with self.assertRaisesRegex(
            NormalizedBatchError, "mutated isolated trusted input"
        ):
            self._exact_case("meta", authentication_context=auth_context)

    def test_named_adapter_metadata_mutation_is_rejected_table_driven(self):
        modules = {
            "meta": meta_module,
            "google": google_module,
            "linkedin": linkedin_module,
            "tiktok": tiktok_module,
            "dv360": dv360_module,
            "ttd": trade_desk_module,
            "xandr": xandr_module,
        }
        for name, module in modules.items():
            original = module.require_closed_object

            def mutate_metadata(
                value,
                keys,
                path,
                _original=original,
                **kwargs,
            ):
                if "reporting_metadata" in path:
                    value["observed_at"] = "2026-08-10T12:00:01Z"
                return _original(value, keys, path, **kwargs)

            def auth_context(
                _module=module,
                _mutator=mutate_metadata,
            ):
                return patch.object(
                    _module, "require_closed_object", _mutator
                )

            with self.subTest(adapter=name):
                with self.assertRaisesRegex(
                    NormalizedBatchError, "mutated isolated trusted input"
                ):
                    self._exact_case(
                        name, authentication_context=auth_context
                    )

    def test_governance_mutation_during_validation_is_rejected(self):
        original = adapter_base_module._minimum_group_size_rule

        def mutate_governance(governance):
            governance["minimum_group_size_rule"] = "999"
            return original(governance)

        def auth_context():
            return patch.object(
                adapter_base_module,
                "_minimum_group_size_rule",
                mutate_governance,
            )

        with self.assertRaisesRegex(
            NormalizedBatchError, "mutated isolated trusted input"
        ):
            self._exact_case("meta", authentication_context=auth_context)

    def test_generic_mapping_and_reporting_mutations_are_rejected(self):
        inputs = self._admit()

        def authenticate():
            return authenticate_normalized_batch(
                authenticated_study=self.study,
                study_authority=self.authority,
                source_inventory=inputs["inventory"],
                admission_validation=inputs["admission"],
                admitted_source=inputs["admitted"],
                governance_input=self.governance,
                profile=inputs["profile"],
                source_manifest=inputs["manifest"],
                import_event_envelope=inputs["envelope"],
            )

        original_closed = generic_module.require_closed_object

        def mutate_reporting(value, keys, path, **kwargs):
            if path == "generic reporting_metadata":
                value["timezone"] = "Forged/Zone"
            return original_closed(value, keys, path, **kwargs)

        with patch.object(
            generic_module, "require_closed_object", mutate_reporting
        ):
            with self.assertRaisesRegex(
                NormalizedBatchError, "mutated isolated trusted input"
            ):
                authenticate()

        original_object = generic_module.require_object

        def mutate_mapping(value, path):
            if path == "generic mapping":
                impressions = next(
                    source
                    for source, target in value.items()
                    if target == "impressions"
                )
                clicks = next(
                    source
                    for source, target in value.items()
                    if target == "clicks"
                )
                value[impressions], value[clicks] = (
                    value[clicks],
                    value[impressions],
                )
            return original_object(value, path)

        with patch.object(generic_module, "require_object", mutate_mapping):
            with self.assertRaisesRegex(
                NormalizedBatchError, "mutated isolated trusted input"
            ):
                authenticate()

    def test_changed_governance_permission_and_access_time_fail(self):
        for field, value in (
            ("permission_reference", "changed-permission"),
            ("export_timestamp", "2026-08-10T12:00:01Z"),
            ("minimum_group_size_rule", "999"),
        ):
            with self.subTest(field=field):
                inputs = self._admit()
                changed = deepcopy(self.governance)
                changed[field] = value
                changed["source_governance_input_sha256"] = None
                changed = validate_source_governance_input(changed)
                with self.assertRaises((ContractError, ValueError)):
                    authenticate_normalized_batch(
                        authenticated_study=self.study,
                        study_authority=self.authority,
                        source_inventory=inputs["inventory"],
                        admission_validation=inputs["admission"],
                        admitted_source=inputs["admitted"],
                        governance_input=changed,
                        profile=inputs["profile"],
                        source_manifest=inputs["manifest"],
                        import_event_envelope=inputs["envelope"],
                    )

    def test_source_hash_substitution_and_fabricated_batch_fail(self):
        batch, inputs = self._batch()
        changed = deepcopy(inputs["manifest"])
        changed["sources"][0]["source_sha256"] = "sha256:" + "0" * 64
        changed["source_manifest_sha256"] = None
        changed = validate_source_manifest(changed)
        with self.assertRaises(NormalizedBatchError):
            authenticate_normalized_batch(
                authenticated_study=self.study,
                study_authority=self.authority,
                source_inventory=inputs["inventory"],
                admission_validation=inputs["admission"],
                admitted_source=inputs["admitted"],
                governance_input=self.governance,
                profile=inputs["profile"],
                source_manifest=changed,
                import_event_envelope=inputs["envelope"],
            )
        fabricated = object.__new__(AuthenticatedNormalizedBatch)
        with self.assertRaises(NormalizedBatchError):
            match_normalized_rows(
                authenticated_batch=fabricated,
                authenticated_study=self.study,
                study_authority=self.authority,
            )
        self.assertIsInstance(batch, AuthenticatedNormalizedBatch)

    def test_physical_row_alias_cannot_be_submitted(self):
        batch, _ = self._batch()
        matched = match_normalized_rows(
            authenticated_batch=batch,
            authenticated_study=self.study,
            study_authority=self.authority,
        )
        alias = deepcopy(matched.matched[0]["normalized_observation"])
        alias["observation_id"] = "caller-alias"
        alias["normalized_observation_sha256"] = None
        alias["normalized_observation_sha256"] = sha256_json(alias)
        with self.assertRaises(TypeError):
            match_normalized_rows(
                rows=[alias],
                registration=self.study.registration,
                delivery_map=self.study.delivery_map,
            )

    def test_name_only_partial_and_cross_provider_identities_quarantine(self):
        cases = {
            "name-only": (
                "meta_ads", "campaign-name", "line-1", "ad-1", "creative-1"
            ),
            "partial": (
                "meta_ads", "23456789012345678901", "other-line",
                "45678901234567890123", "45678901234567890123",
            ),
            "cross-provider": (
                "google_ads", "23456789012345678901", "34567890123456789012",
                "45678901234567890123", "45678901234567890123",
            ),
        }
        for name, identity in cases.items():
            with self.subTest(case=name):
                study, authority, batch, _, _ = self._exact_case(
                    "meta", mapping_override=identity
                )
                matched = match_normalized_rows(
                    authenticated_batch=batch,
                    authenticated_study=study,
                    study_authority=authority,
                )
                self.assertEqual((), matched.matched)
                self.assertEqual(
                    "identity_not_sealed", matched.quarantined[0]["reason"]
                )

    def test_descriptive_authenticated_study_cannot_be_upgraded(self):
        fixture = study_tests.OutcomePrepStudyGoldenPaths(
            "test_post_outcome_delivery_mapping_is_permanently_descriptive"
        )
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        fixture.supplied_facts["registered_at"] = "2026-07-31T12:00:00Z"
        fixture.supplied_facts["delivery_map_sealed_at"] = (
            "2026-07-31T12:00:00Z"
        )
        fixture.supplied_facts["first_outcome_accessed_at"] = (
            "2026-07-31T11:00:00Z"
        )
        descriptive = fixture._seal(fixture._draft(), fixture.root / "desc")
        study, authority = fixture._authenticate(descriptive.study_root)
        self.assertEqual("descriptive_only", study.evidence_status)
        changed = deepcopy(study)
        changed.registration_receipt["evidence_status"] = (
            "preregistered_holdout"
        )
        with self.assertRaises(ContractError):
            verify_study_authority(changed, authority=authority)
        original = self.study, self.authority
        self.study, self.authority = study, authority
        try:
            with self.assertRaises((ContractError, ValueError)):
                self._batch()
        finally:
            self.study, self.authority = original

    def test_study_capability_rejects_a_substituted_ledger_digest(self):
        changed = replace(self.study, ledger_digest="sha256:" + "0" * 64)
        with self.assertRaises(ContractError):
            verify_study_authority(changed, authority=self.authority)

    def test_duplicate_delivery_and_every_sealed_analytical_identity_tamper_fail(self):
        batch, _ = self._batch()
        duplicate = deepcopy(self.study)
        duplicate.delivery_map["mappings"].append(
            deepcopy(duplicate.delivery_map["mappings"][0])
        )
        duplicate.delivery_map["delivery_map_sha256"] = None
        duplicate.delivery_map["delivery_map_sha256"] = sha256_json(
            duplicate.delivery_map
        )
        with self.assertRaises(ValueError):
            match_normalized_rows(
                authenticated_batch=batch,
                authenticated_study=duplicate,
                study_authority=self.authority,
            )

        fields = {
            "platform": "other_platform",
            "platform_campaign_id": "other-campaign",
            "platform_ad_group_id": "other-group",
            "platform_ad_id": "other-ad",
            "platform_creative_id": "other-creative",
            "block_id": "other-block",
            "study_id": "other-study",
            "arm_id": "other-arm",
            "batch_id": "other-batch",
            "segment_ids": ["other-segment"],
            "creative_id": "creative-b",
            "variant_id": "other-variant",
            "asset_sha256": "sha256:" + "0" * 64,
            "campaign_plan_sha256": "sha256:" + "1" * 64,
        }
        for field, value in fields.items():
            with self.subTest(field=field):
                changed = deepcopy(self.study)
                changed.delivery_map["mappings"][0][field] = value
                changed.delivery_map["delivery_map_sha256"] = None
                changed.delivery_map["delivery_map_sha256"] = sha256_json(
                    changed.delivery_map
                )
                with self.assertRaises(ValueError):
                    match_normalized_rows(
                        authenticated_batch=batch,
                        authenticated_study=changed,
                        study_authority=self.authority,
                    )

    def test_admitted_bytes_are_reopened_before_every_match(self):
        batch, inputs = self._batch()
        inputs["admitted"].source_path.write_bytes(b"changed after admission\n")
        with self.assertRaises((ContractError, ValueError)):
            match_normalized_rows(
                authenticated_batch=batch,
                authenticated_study=self.study,
                study_authority=self.authority,
            )

    def test_handoff_is_deterministic_and_contains_no_decision_fields(self):
        batch, inputs = self._batch()
        observation = build_validation_observation(
            observation_id=inputs["observation_id"],
            authenticated_batch=batch,
            authenticated_study=self.study,
            study_authority=self.authority,
        )
        handoff = validate_validation_handoff(
            authenticated_batch=batch,
            authenticated_study=self.study,
            study_authority=self.authority,
            validation_observations=[observation],
        )
        self.assertEqual(1, len(handoff["validation_observations"]))
        self.assertTrue({
            "comparison",
            "observed_ordering",
            "claim_family",
            "evaluation",
            "calibration",
            "activation",
        }.isdisjoint(handoff))

    def test_unchanged_comparison_accepts_reversed_input_and_rejects_tamper(self):
        fixture = AuthenticatedFixture(self, temporary_parent=ROOT)
        observations = fixture.observations()
        forward = fixture.compare(observations)
        reverse = fixture.compare(list(reversed(observations)))
        self.assertEqual(forward, reverse)
        tampered = deepcopy(observations)
        tampered[0]["aggregate"]["success_count"] = 99
        with self.assertRaises(ValueError):
            fixture.compare(tampered)


if __name__ == "__main__":
    unittest.main()
