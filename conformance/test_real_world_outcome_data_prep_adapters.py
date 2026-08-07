from __future__ import annotations

import csv
from dataclasses import replace
from io import BytesIO, StringIO
import json
from pathlib import Path
import re
import sys
import tempfile
import unittest

from openpyxl import Workbook


ROOT = Path(__file__).resolve().parents[1]
SKILL_ROOT = ROOT / "skills" / "real-world-outcome-data-prep"
SCRIPTS = SKILL_ROOT / "scripts"
REGISTRY_PATH = SKILL_ROOT / "references" / "platform-capabilities.json"
sys.path.insert(0, str(SCRIPTS))

from outcome_data_prep.adapters.base import (  # noqa: E402
    AdapterError,
    AdapterInventory,
    AdapterResult,
    AdapterValidation,
    ExactVariantAdapter,
    OutcomeAdapter,
)
from outcome_data_prep.adapters.google_ads import GoogleAdsAdapter  # noqa: E402
from outcome_data_prep.adapters.amazon_dsp import (  # noqa: E402
    AmazonDSPAdapter,
    amazon_variant_available,
)
from outcome_data_prep.adapters.dv360 import (  # noqa: E402
    DV360Adapter,
    require_dv360_report_context,
)
from outcome_data_prep.adapters.generic_programmatic import (  # noqa: E402
    GenericProgrammaticAdapter,
    validate_generic_mapping,
)
from outcome_data_prep.adapters.linkedin import LinkedInAdsAdapter  # noqa: E402
from outcome_data_prep.adapters.meta import MetaInsightsAdapter  # noqa: E402
from outcome_data_prep.adapters.tiktok import TikTokAdsAdapter  # noqa: E402
from outcome_data_prep.adapters.trade_desk import (  # noqa: E402
    TradeDeskAdapter,
    require_ttd_report_identity,
)
from outcome_data_prep.adapters.xandr import (  # noqa: E402
    XandrAdapter,
    xandr_creative_state,
)
from outcome_data_prep.capabilities import (  # noqa: E402
    AdapterCapability,
    CapabilityRegistryError,
    Detection,
    load_capability_registry,
    resolve_adapter,
)
from outcome_data_prep.common import ContractError, sha256_json  # noqa: E402
from outcome_data_prep.contracts import (  # noqa: E402
    validate_normalized_observation,
)
from outcome_data_prep.container_safety import (  # noqa: E402
    ContainerInventory,
    InventoryCell,
    inspect_container,
)
from outcome_data_prep.privacy import (  # noqa: E402
    AdmittedSource,
    AdapterAdmissionValidation,
    PrivacyAdmissionError,
    adapter_admission_validation_sha256,
    admit_source,
    container_inventory_sha256,
    pre_scan_obvious_privacy,
    privacy_decision_sha256,
    source_snapshot_sha256,
)
from outcome_data_prep.source_snapshot import snapshot_source  # noqa: E402


def inventory(
    *,
    media_type: str,
    headers: tuple[str, ...],
    rows: tuple[tuple[str, ...], ...] = (),
    table: str = "report",
) -> ContainerInventory:
    return ContainerInventory(
        media_type=media_type,
        tables=(table,),
        headers=(headers,),
        cells=tuple(
            InventoryCell(
                table=table,
                row_number=row_number,
                column_name=header,
                value=value,
            )
            for row_number, row in enumerate(rows, start=2)
            for header, value in zip(headers, row, strict=True)
        ),
        row_count=len(rows),
    )


class AdapterCapabilityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.registry = load_capability_registry(REGISTRY_PATH)

    def capability(self, adapter_id: str) -> AdapterCapability:
        matches = [
            record for record in self.registry
            if record.adapter_id == adapter_id
        ]
        self.assertEqual(1, len(matches))
        return matches[0]

    def test_registry_covers_every_required_exact_physical_variant(self):
        required = {
            "meta-insights-api-json-v1",
            "meta-ads-manager-csv-en-us-v1",
            "meta-ads-manager-xlsx-en-us-v1",
            "google-ads-api-v23-ad-daily-json",
            "google-ads-ui-csv-en-us-v1",
            "google-ads-ui-excel-csv-en-us-v1",
            "google-ads-ui-tsv-en-us-v1",
            "google-ads-ui-xlsx-en-us-v1",
            "google-ads-ui-xml-en-us-v1",
            "linkedin-ads-reporting-api-json-v1",
            "linkedin-campaign-manager-csv-en-us-v1",
            "tiktok-reporting-api-json-v1",
            "tiktok-custom-report-csv-en-us-v1",
            "tiktok-custom-report-xlsx-en-us-v1",
            "dv360-bid-manager-v2-standard-csv-v1",
            "dv360-bid-manager-v2-standard-xlsx-v1",
            "trade-desk-report-template-csv-v1",
            "trade-desk-report-template-tsv-v1",
            "trade-desk-report-type-xlsx-v1",
            "amazon-unified-reporting-ui-csv-v1",
            "amazon-unified-reporting-ui-xlsx-v1",
            "amazon-unified-reporting-api-json-v1",
            "xandr-advertiser-analytics-csv-v1",
            "xandr-advertiser-analytics-excel-tsv-v1",
            "xandr-advertiser-analytics-xlsx-v1",
            "generic-dsp-mapping-v1",
        }
        self.assertEqual(required, {record.adapter_id for record in self.registry})

    def test_schema_tested_variant_cannot_claim_export_verified(self):
        record = self.capability("google-ads-ui-csv-en-us-v1")
        self.assertEqual("schema_tested", record.maturity)
        self.assertFalse(record.contract_ready_permitted)
        self.assertIsNone(record.reviewer)
        self.assertIsNone(record.verified_at)

    def test_no_seeded_variant_claims_export_verified(self):
        self.assertNotIn(
            "export_verified",
            {record.maturity for record in self.registry},
        )
        self.assertTrue(
            all(not record.contract_ready_permitted for record in self.registry)
        )

    def test_admissible_programmatic_variants_exclude_business_outcomes(self):
        prohibited = {
            "revenue",
            "sales",
            "retention",
            "crm",
            "analytics",
            "customer",
            "purchase_value",
            "order_value",
            "lifetime_value",
            "ltv",
        }
        for record in self.registry:
            if record.platform not in {"dv360", "the_trade_desk"}:
                continue
            admitted = (
                record.identity_fields
                + record.required_fields
                + record.metric_fields
            )
            normalized = {
                re.sub(r"[^a-z0-9]+", "_", field.lower()).strip("_")
                for field in admitted
            }
            with self.subTest(adapter_id=record.adapter_id):
                self.assertFalse(
                    any(
                        token == field
                        or token in field.split("_")
                        or field.endswith(f"_{token}")
                        for field in normalized
                        for token in prohibited
                    ),
                    admitted,
                )

    def test_blocked_variants_use_exact_reason(self):
        blocked = [
            record for record in self.registry
            if record.maturity == "blocked"
        ]
        self.assertTrue(blocked)
        self.assertTrue(
            all(
                record.availability_reason
                == "blocked_pending_sanitized_sample"
                for record in blocked
            )
        )

    def test_every_fingerprint_is_derived_from_its_exact_allowlist(self):
        for record in self.registry:
            fields = sorted(
                set(record.identity_fields)
                | set(record.required_fields)
                | set(record.metric_fields)
            )
            self.assertEqual(sha256_json(fields), record.schema_fingerprint)

    def test_google_fingerprint_matches_frozen_value(self):
        record = self.capability("google-ads-api-v23-ad-daily-json")
        self.assertEqual(
            "sha256:234b3aceb30983432fedeabb5bc026bf"
            "aaeddbac26aa4459c3220e95d59b475f",
            record.schema_fingerprint,
        )

    def test_registry_rejects_unknown_fields_and_fingerprint_forgery(self):
        raw = json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))
        for mutation, message in (
            (lambda record: record.__setitem__("provider_hint", "google"), "unknown"),
            (
                lambda record: record.__setitem__(
                    "schema_fingerprint", "sha256:" + "0" * 64
                ),
                "fingerprint",
            ),
        ):
            forged = json.loads(json.dumps(raw))
            mutation(forged[0])
            with tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / "registry.json"
                path.write_text(json.dumps(forged), encoding="utf-8")
                with self.assertRaisesRegex(CapabilityRegistryError, message):
                    load_capability_registry(path)

    def test_loader_rejects_exact_signature_collision(self):
        raw = json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))
        collision = dict(raw[0])
        collision["adapter_id"] = "collision-adapter-v1"
        raw.append(collision)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "registry.json"
            path.write_text(json.dumps(raw), encoding="utf-8")
            with self.assertRaisesRegex(
                CapabilityRegistryError, "exact signature collision"
            ):
                load_capability_registry(path)

    def test_loader_rejects_duplicate_json_keys(self):
        raw = REGISTRY_PATH.read_text(encoding="utf-8")
        forged = raw.replace(
            '"adapter_id": "meta-insights-api-json-v1",',
            '"adapter_id": "first-value", '
            '"adapter_id": "meta-insights-api-json-v1",',
            1,
        )
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "registry.json"
            path.write_text(forged, encoding="utf-8")
            with self.assertRaisesRegex(
                CapabilityRegistryError, "duplicate JSON key"
            ):
                load_capability_registry(path)


class AdapterDispatchTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.registry = load_capability_registry(REGISTRY_PATH)
        cls.google_capability = next(
            record for record in cls.registry
            if record.adapter_id == "google-ads-api-v23-ad-daily-json"
        )
        cls.google_headers = tuple(
            cls.google_capability.identity_fields
            + cls.google_capability.required_fields
            + cls.google_capability.metric_fields
        )

    def test_header_fingerprint_selects_one_variant(self):
        detection = resolve_adapter(
            inventory(
                media_type="application/json",
                headers=self.google_headers,
            ),
            self.registry,
        )
        self.assertEqual(
            "google-ads-api-v23-ad-daily-json", detection.adapter_id
        )
        self.assertEqual("schema_tested", detection.status)
        self.assertEqual("exact", detection.confidence)

    def test_header_order_does_not_change_exact_selection(self):
        detection = resolve_adapter(
            inventory(
                media_type="application/json",
                headers=tuple(reversed(self.google_headers)),
            ),
            self.registry,
        )
        self.assertEqual(
            "google-ads-api-v23-ad-daily-json", detection.adapter_id
        )

    def test_provider_name_or_filename_hint_never_proves_compatibility(self):
        detection = resolve_adapter(
            inventory(
                media_type="text/csv",
                headers=("Google Ads", "provider", "report_name"),
                table="google-ads-api-v23-ad-daily-json.csv",
            ),
            self.registry,
        )
        self.assertIsNone(detection.adapter_id)
        self.assertEqual("unsupported_exact_variant", detection.status)
        self.assertEqual("none", detection.confidence)

    def test_platform_name_without_exact_variant_is_not_support(self):
        record = next(
            capability for capability in self.registry
            if capability.adapter_id == "meta-ads-manager-csv-en-us-v1"
        )
        headers = tuple(
            record.identity_fields
            + record.required_fields
            + record.metric_fields
        )
        detection = resolve_adapter(
            inventory(media_type="text/csv", headers=headers),
            self.registry,
        )
        self.assertEqual(
            "blocked_pending_sanitized_sample", detection.status
        )
        self.assertEqual(record.adapter_id, detection.adapter_id)

    def test_extra_or_missing_header_fails_closed(self):
        for headers in (
            self.google_headers[:-1],
            self.google_headers + ("provider",),
        ):
            with self.subTest(headers=headers):
                detection = resolve_adapter(
                    inventory(
                        media_type="application/json",
                        headers=headers,
                    ),
                    self.registry,
                )
                self.assertIsNone(detection.adapter_id)
                self.assertEqual(
                    "unsupported_exact_variant", detection.status
                )

    def test_resolver_fails_closed_on_runtime_collision(self):
        collision = replace(
            self.google_capability,
            adapter_id="collision-adapter-v1",
        )
        detection = resolve_adapter(
            inventory(
                media_type="application/json",
                headers=self.google_headers,
            ),
            self.registry + (collision,),
        )
        self.assertIsNone(detection.adapter_id)
        self.assertEqual("ambiguous_exact_variant", detection.status)
        self.assertIn("exact_signature_collision", detection.reasons)


class AdapterProtocolTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.registry = load_capability_registry(REGISTRY_PATH)
        cls.capability = next(
            record for record in cls.registry
            if record.adapter_id == "google-ads-api-v23-ad-daily-json"
        )
        cls.adapter = ExactVariantAdapter(cls.capability)
        cls.headers = tuple(
            cls.capability.identity_fields
            + cls.capability.required_fields
            + cls.capability.metric_fields
        )
        cls.registration = {"registration_id": "registration-1"}
        cls.governance = {"minimum_group_size_rule": "10"}

    def container(
        self,
        *,
        include_group_size: bool = True,
        include_prohibited: bool = False,
        group_size: str = "25",
    ) -> ContainerInventory:
        headers = list(self.headers)
        if not include_group_size:
            headers.remove(self.capability.group_size_field)
        if include_prohibited:
            headers.append("email")
        values = {
            "customer.id": "12345678901234567890",
            "campaign.id": "23456789012345678901",
            "ad_group.id": "34567890123456789012",
            "ad_group_ad.ad.id": "45678901234567890123",
            "segments.date": "2026-07-01",
            "metrics.impressions": group_size,
            "metrics.clicks": "3",
            "metrics.cost_micros": "1250000",
            "metrics.conversions": "1.5",
            "metrics.all_conversions": "2.0",
            "email": "redacted@example.invalid",
        }
        return inventory(
            media_type="application/json",
            headers=tuple(headers),
            rows=(tuple(values[header] for header in headers),),
        )

    def xlsx_capability(self) -> AdapterCapability:
        return next(
            record for record in self.registry
            if record.adapter_id == "google-ads-ui-xlsx-en-us-v1"
        )

    @staticmethod
    def multitable_xlsx(
        capability: AdapterCapability,
        table_headers: tuple[tuple[str, ...], ...],
    ) -> ContainerInventory:
        tables = tuple(
            f"Sheet{index}" for index in range(1, len(table_headers) + 1)
        )
        return ContainerInventory(
            media_type=(
                "application/vnd.openxmlformats-officedocument."
                "spreadsheetml.sheet"
            ),
            tables=tables,
            headers=table_headers,
            cells=tuple(
                InventoryCell(
                    table=table,
                    row_number=2,
                    column_name=header,
                    value=(
                        str(20 + table_index)
                        if header == capability.group_size_field
                        else f"value-{table_index}"
                    ),
                )
                for table_index, (table, headers) in enumerate(
                    zip(tables, table_headers, strict=True),
                    start=1,
                )
                for header in headers
            ),
            row_count=len(tables),
        )

    def test_every_adapter_implements_all_five_closed_stages(self):
        self.assertIsInstance(self.adapter, OutcomeAdapter)
        for stage in ("detect", "inventory", "validate", "normalize", "explain"):
            self.assertTrue(callable(getattr(self.adapter, stage)))

    def test_missing_registered_group_size_field_is_not_admitted(self):
        source = self.container(include_group_size=False)
        adapter_inventory = self.adapter.inventory(
            source, self.capability, require_exact_schema=False
        )
        validation = self.adapter.validate(
            adapter_inventory,
            registration=self.registration,
            governance=self.governance,
            capability=self.capability,
        )
        self.assertFalse(validation.accepted)
        self.assertIn("missing_registered_group_size", validation.errors)
        with self.assertRaisesRegex(AdapterError, "not accepted"):
            self.adapter.admission_validation(
                source,
                source_sha256="sha256:" + "1" * 64,
                validation=validation,
                registration=self.registration,
                governance=self.governance,
            )

    def test_google_customer_and_ad_ids_are_platform_identities(self):
        source = self.container()
        adapter_inventory = self.adapter.inventory(source, self.capability)
        validation = self.adapter.validate(
            adapter_inventory,
            registration=self.registration,
            governance=self.governance,
            capability=self.capability,
        )
        self.assertTrue(validation.accepted)
        self.assertEqual(25, validation.observed_minimum_group_size)

    def test_prohibited_adapter_field_is_rejected_before_admission(self):
        source = self.container(include_prohibited=True)
        adapter_inventory = self.adapter.inventory(
            source, self.capability, require_exact_schema=False
        )
        validation = self.adapter.validate(
            adapter_inventory,
            registration=self.registration,
            governance=self.governance,
            capability=self.capability,
        )
        self.assertFalse(validation.accepted)
        self.assertIn("prohibited_field", validation.errors)

    def test_unparseable_or_small_registered_group_is_rejected(self):
        for value, expected in (
            ("not-a-number", "unparseable_registered_group_size"),
            ("9", "minimum_group_size_below_rule"),
        ):
            with self.subTest(value=value):
                source = self.container(group_size=value)
                adapter_inventory = self.adapter.inventory(
                    source, self.capability
                )
                validation = self.adapter.validate(
                    adapter_inventory,
                    registration=self.registration,
                    governance=self.governance,
                    capability=self.capability,
                )
                self.assertFalse(validation.accepted)
                self.assertIn(expected, validation.errors)

    def assert_structurally_rejected(
        self, source: ContainerInventory
    ) -> None:
        adapter_inventory = self.adapter.inventory(source, self.capability)
        validation = self.adapter.validate(
            adapter_inventory,
            registration=self.registration,
            governance=self.governance,
            capability=self.capability,
        )
        self.assertFalse(validation.accepted)
        self.assertIn("malformed_inventory_structure", validation.errors)
        with self.assertRaisesRegex(AdapterError, "not accepted"):
            self.adapter.admission_validation(
                source,
                source_sha256="sha256:" + "1" * 64,
                validation=validation,
                registration=self.registration,
                governance=self.governance,
            )

    def test_declared_row_without_denominator_cell_is_rejected(self):
        source = self.container()
        second_row_without_denominator = tuple(
            replace(cell, row_number=2)
            for cell in source.cells
            if cell.column_name != self.capability.group_size_field
        )
        malformed = replace(
            source,
            cells=source.cells + second_row_without_denominator,
            row_count=2,
        )
        self.assert_structurally_rejected(malformed)

    def test_declared_row_count_cannot_exceed_observed_complete_rows(self):
        source = self.container()
        self.assert_structurally_rejected(replace(source, row_count=2))

    def test_duplicate_denominator_cell_is_rejected(self):
        source = self.container()
        denominator = next(
            cell for cell in source.cells
            if cell.column_name == self.capability.group_size_field
        )
        malformed = replace(
            source,
            cells=source.cells + (denominator,),
        )
        self.assert_structurally_rejected(malformed)

    def test_denominator_cell_with_inconsistent_membership_is_rejected(self):
        source = self.container()
        for changed_cell in (
            {"table": "undeclared-table"},
            {"column_name": "undeclared-denominator"},
        ):
            with self.subTest(changed_cell=changed_cell):
                malformed = replace(
                    source,
                    cells=tuple(
                        replace(cell, **changed_cell)
                        if cell.column_name
                        == self.capability.group_size_field
                        else cell
                        for cell in source.cells
                    ),
                )
                self.assert_structurally_rejected(malformed)

    def test_multitable_variant_rejects_sheet_missing_exact_header_set(self):
        capability = self.xlsx_capability()
        adapter = ExactVariantAdapter(capability)
        exact_headers = tuple(
            capability.identity_fields
            + capability.required_fields
            + capability.metric_fields
        )
        denominator = capability.group_size_field
        remaining = tuple(
            header for header in exact_headers if header != denominator
        )
        split = len(remaining) // 2
        header_shapes = (
            (
                (denominator,) + remaining[:split],
                remaining[split:],
            ),
            (exact_headers, remaining),
        )
        for table_headers in header_shapes:
            with self.subTest(table_headers=table_headers):
                source = self.multitable_xlsx(
                    capability,
                    table_headers,
                )
                self.assertIsNone(adapter.detect(source).adapter_id)
                adapter_inventory = adapter.inventory(
                    source, capability, require_exact_schema=False
                )
                validation = adapter.validate(
                    adapter_inventory,
                    registration=self.registration,
                    governance=self.governance,
                    capability=capability,
                )
                self.assertFalse(validation.accepted)
                self.assertIn(
                    "malformed_inventory_structure", validation.errors
                )
                with self.assertRaisesRegex(AdapterError, "not accepted"):
                    adapter.admission_validation(
                        source,
                        source_sha256="sha256:" + "1" * 64,
                        validation=validation,
                        registration=self.registration,
                        governance=self.governance,
                    )

    def test_valid_multitable_exact_schema_is_detected_and_admitted(self):
        capability = self.xlsx_capability()
        adapter = ExactVariantAdapter(capability)
        exact_headers = tuple(
            capability.identity_fields
            + capability.required_fields
            + capability.metric_fields
        )
        source = self.multitable_xlsx(
            capability,
            (exact_headers, tuple(reversed(exact_headers))),
        )
        detection = adapter.detect(source)
        self.assertEqual(capability.adapter_id, detection.adapter_id)
        adapter_inventory = adapter.inventory(source, capability)
        validation = adapter.validate(
            adapter_inventory,
            registration=self.registration,
            governance=self.governance,
            capability=capability,
        )
        self.assertTrue(validation.accepted)
        admission = adapter.admission_validation(
            source,
            source_sha256="sha256:" + "1" * 64,
            validation=validation,
            registration=self.registration,
            governance=self.governance,
        )
        self.assertTrue(admission.accepted)
        self.assertEqual(21, admission.observed_minimum_group_size)

    def test_accepted_validation_mints_source_and_inventory_bound_record(self):
        source = self.container()
        adapter_inventory = self.adapter.inventory(source, self.capability)
        validation = self.adapter.validate(
            adapter_inventory,
            registration=self.registration,
            governance=self.governance,
            capability=self.capability,
        )
        source_sha256 = "sha256:" + "1" * 64
        admission = self.adapter.admission_validation(
            source,
            source_sha256=source_sha256,
            validation=validation,
            registration=self.registration,
            governance=self.governance,
        )
        self.assertIsInstance(admission, AdapterAdmissionValidation)
        self.assertEqual(self.capability.adapter_id, admission.adapter_id)
        self.assertEqual(self.capability.adapter_version, admission.adapter_version)
        self.assertEqual(source_sha256, admission.source_sha256)
        self.assertEqual(
            container_inventory_sha256(source), admission.inventory_sha256
        )
        self.assertTrue(admission.accepted)
        self.assertEqual(25, admission.observed_minimum_group_size)
        self.assertEqual((), admission.errors)

    def test_validation_cannot_be_replayed_onto_another_inventory(self):
        source = self.container()
        validation = self.adapter.validate(
            self.adapter.inventory(source, self.capability),
            registration=self.registration,
            governance=self.governance,
            capability=self.capability,
        )
        changed = self.container(group_size="26")
        with self.assertRaisesRegex(AdapterError, "inventory binding"):
            self.adapter.admission_validation(
                changed,
                source_sha256="sha256:" + "1" * 64,
                validation=validation,
                registration=self.registration,
                governance=self.governance,
            )

    def test_caller_forged_accepted_validation_cannot_mint_admission(self):
        source = self.container(group_size="9")
        forged = AdapterValidation(
            accepted=True,
            errors=(),
            warnings=(),
            observed_minimum_group_size=999,
            inventory_sha256=container_inventory_sha256(source),
        )
        with self.assertRaisesRegex(AdapterError, "validated result"):
            self.adapter.admission_validation(
                source,
                source_sha256="sha256:" + "1" * 64,
                validation=forged,
                registration=self.registration,
                governance=self.governance,
            )

    def test_base_normalize_fails_without_platform_semantics(self):
        source = self.container()
        with self.assertRaisesRegex(AdapterError, "platform adapter"):
            self.adapter.normalize(
                self.adapter.inventory(source, self.capability),
                registration=self.registration,
                capability=self.capability,
            )

    def test_protocol_records_are_frozen(self):
        for record_type in (
            AdapterCapability,
            Detection,
            AdapterInventory,
            AdapterResult,
        ):
            self.assertTrue(record_type.__dataclass_params__.frozen)


class MetaAndGoogleAdapterTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        registry = load_capability_registry(REGISTRY_PATH)
        cls.meta_capability = next(
            record
            for record in registry
            if record.adapter_id == "meta-insights-api-json-v1"
        )
        cls.google_capability = next(
            record
            for record in registry
            if record.adapter_id == "google-ads-api-v23-ad-daily-json"
        )
        cls.blocked_meta_capability = next(
            record
            for record in registry
            if record.adapter_id == "meta-ads-manager-csv-en-us-v1"
        )

    def setUp(self):
        self.meta = MetaInsightsAdapter(self.meta_capability)
        self.google = GoogleAdsAdapter(self.google_capability)
        self.meta_registration = {
            "study_id": "study-1",
            "registration_id": "registration-1",
            "metric_id": "purchase",
            "conversion_event_key": "offsite_conversion.purchase",
            "request_level": "ad",
            "action_report_time": "conversion",
            "attribution_windows": ["7d_click", "1d_view"],
            "time_increment": "1",
        }
        self.google_registration = {
            "study_id": "study-1",
            "registration_id": "registration-1",
            "metric_id": "purchase",
            "registered_source_metric": "conversions",
            "time_basis": "interaction_date",
            "segment_grain": [
                "customer.id",
                "campaign.id",
                "ad_group.id",
                "ad_group_ad.ad.id",
                "segments.date",
            ],
        }
        self.meta_fixture = {
            "source_id": "source-meta-1",
            "import_id": "import-1",
            "source_sha256": "sha256:" + "a" * 64,
            "reporting_metadata": {
                "account_currency": "USD",
                "account_timezone": "America/New_York",
                "request_level": "ad",
                "action_report_time": "conversion",
                "attribution_windows": ["7d_click", "1d_view"],
                "time_increment": "1",
                "reporting_basis": "account_reporting_day",
                "latency_state": "mature",
                "conversion_value_state": "observed",
                "observed_at": "2026-07-10T12:00:00-04:00",
            },
            "rows": [
                {
                    "source_row_reference": "report:2",
                    "account_id": "act_12345678901234567890",
                    "campaign_id": "23456789012345678901",
                    "adset_id": "34567890123456789012",
                    "ad_id": "45678901234567890123",
                    "date_start": "2026-07-01",
                    "date_stop": "2026-07-01",
                    "impressions": "1000",
                    "clicks": "12",
                    "spend": "123.4500",
                    "actions": [
                        {"action_type": "link_click", "value": "12"},
                        {
                            "action_type": "offsite_conversion.purchase",
                            "value": "3.50",
                        },
                    ],
                }
            ],
        }
        self.google_fixture = {
            "source_id": "source-google-1",
            "import_id": "import-1",
            "source_sha256": "sha256:" + "b" * 64,
            "reporting_metadata": {
                "customer_currency": "USD",
                "customer_time_zone": "America/New_York",
                "time_basis": "interaction_date",
                "segment_grain": [
                    "customer.id",
                    "campaign.id",
                    "ad_group.id",
                    "ad_group_ad.ad.id",
                    "segments.date",
                ],
                "omitted_zero_behavior": "rows_omitted_when_all_metrics_zero",
                "latency_state": "mature",
                "conversion_value_state": "observed",
                "observed_at": "2026-07-10T12:00:00-04:00",
            },
            "rows": [
                {
                    "source_row_reference": "results[0]",
                    "customer": {"id": "12345678901234567890"},
                    "campaign": {"id": "23456789012345678901"},
                    "ad_group": {"id": "34567890123456789012"},
                    "ad_group_ad": {
                        "ad": {"id": "45678901234567890123"}
                    },
                    "segments": {"date": "2026-07-01"},
                    "metrics": {
                        "impressions": "1000",
                        "clicks": "12",
                        "cost_micros": "123456789",
                        "conversions": "1.5",
                        "all_conversions": "2.25",
                    },
                }
            ],
        }

    def test_meta_requires_one_conversion_event_key(self):
        registration = dict(self.meta_registration)
        registration.pop("conversion_event_key")
        with self.assertRaisesRegex(AdapterError, "conversion_event_key"):
            self.meta.normalize(
                self.meta_fixture,
                registration=registration,
                capability=self.meta_capability,
            )

    def test_meta_selects_only_registered_action_and_preserves_semantics(self):
        result = self.meta.normalize(
            self.meta_fixture,
            registration=self.meta_registration,
            capability=self.meta_capability,
        )
        row = result.normalized_rows[0]
        self.assertEqual(
            "act_12345678901234567890", row["account"]["platform_id"]
        )
        self.assertEqual(
            "45678901234567890123", row["creative"]["platform_id"]
        )
        self.assertEqual("report:2", row["source_row_reference"])
        self.assertEqual("123.4500", row["spend"]["source_numeric_text"])
        self.assertEqual("123.4500", row["spend"]["decimal"])
        self.assertEqual(3.5, row["outcome"]["value"])
        self.assertEqual("3.50", row["outcome"]["source_numeric_text"])
        self.assertEqual(
            "offsite_conversion.purchase",
            row["outcome"]["source_metric"],
        )
        self.assertEqual("fractional", row["outcome"]["value_state"])
        self.assertEqual(
            ["7d_click", "1d_view"],
            row["attribution"]["windows"],
        )
        self.assertEqual("conversion", row["attribution"]["report_time"])
        self.assertEqual("America/New_York", row["reporting"]["timezone"])
        self.assertEqual("account_reporting_day", row["reporting"]["basis"])
        self.assertEqual("USD", row["currency"]["code"])
        self.assertEqual("schema_tested", result.maturity)
        self.assertFalse(result.mapping_report["contract_ready"])
        json.dumps(result.normalized_rows)

    def test_meta_rejects_missing_or_duplicate_selected_action(self):
        missing = json.loads(json.dumps(self.meta_fixture))
        missing["rows"][0]["actions"] = [
            {"action_type": "link_click", "value": "12"}
        ]
        duplicate = json.loads(json.dumps(self.meta_fixture))
        duplicate["rows"][0]["actions"].append(
            {
                "action_type": "offsite_conversion.purchase",
                "value": "1",
            }
        )
        for payload in (missing, duplicate):
            with self.subTest(payload=payload):
                with self.assertRaisesRegex(
                    AdapterError,
                    "conversion_event_key must select exactly one Meta action",
                ):
                    self.meta.normalize(
                        payload,
                        registration=self.meta_registration,
                        capability=self.meta_capability,
                    )

    def test_meta_rejects_malformed_unselected_action_values(self):
        for value in (None, "<5", "NaN", "-1"):
            with self.subTest(value=value):
                payload = json.loads(json.dumps(self.meta_fixture))
                payload["rows"][0]["actions"][0]["value"] = value
                with self.assertRaisesRegex(AdapterError, "actions.*value"):
                    self.meta.normalize(
                        payload,
                        registration=self.meta_registration,
                        capability=self.meta_capability,
                    )

    def test_meta_rejects_reporting_configuration_drift(self):
        for field, changed in (
            ("request_level", "campaign"),
            ("action_report_time", "impression"),
            ("attribution_windows", ["28d_click"]),
            ("time_increment", "all_days"),
        ):
            with self.subTest(field=field):
                payload = json.loads(json.dumps(self.meta_fixture))
                payload["reporting_metadata"][field] = changed
                with self.assertRaisesRegex(AdapterError, re.escape(field)):
                    self.meta.normalize(
                        payload,
                        registration=self.meta_registration,
                        capability=self.meta_capability,
                    )

    def test_google_preserves_fractional_conversions_and_cost_micros(self):
        result = self.google.normalize(
            self.google_fixture,
            registration=self.google_registration,
            capability=self.google_capability,
        )
        row = result.normalized_rows[0]
        self.assertEqual("123.456789", row["spend"]["decimal"])
        self.assertEqual(
            "123456789", row["spend"]["source_numeric_text"]
        )
        self.assertEqual("cost_micros", row["spend"]["source_metric"])
        self.assertEqual(1.5, row["outcome"]["value"])
        self.assertEqual("1.5", row["outcome"]["source_numeric_text"])
        self.assertEqual("conversions", row["outcome"]["source_metric"])
        self.assertEqual("fractional", row["outcome"]["value_state"])
        self.assertEqual("results[0]", row["source_row_reference"])

    def test_google_does_not_substitute_all_conversions(self):
        payload = json.loads(json.dumps(self.google_fixture))
        del payload["rows"][0]["metrics"]["conversions"]
        with self.assertRaisesRegex(AdapterError, "registered source metric"):
            self.google.normalize(
                payload,
                registration=self.google_registration,
                capability=self.google_capability,
            )

    def test_google_can_select_all_conversions_without_using_conversions(self):
        registration = dict(self.google_registration)
        registration["registered_source_metric"] = "all_conversions"
        result = self.google.normalize(
            self.google_fixture,
            registration=registration,
            capability=self.google_capability,
        )
        row = result.normalized_rows[0]
        self.assertEqual("all_conversions", row["outcome"]["source_metric"])
        self.assertEqual("2.25", row["outcome"]["source_numeric_text"])

    def test_google_rejects_malformed_unselected_conversion_values(self):
        for value in (None, "malformed", "Infinity", "-1"):
            with self.subTest(value=value):
                payload = json.loads(json.dumps(self.google_fixture))
                payload["rows"][0]["metrics"]["all_conversions"] = value
                with self.assertRaisesRegex(
                    AdapterError, "metrics.all_conversions"
                ):
                    self.google.normalize(
                        payload,
                        registration=self.google_registration,
                        capability=self.google_capability,
                    )

    def test_google_preserves_identity_grain_currency_time_and_latency(self):
        row = self.google.normalize(
            self.google_fixture,
            registration=self.google_registration,
            capability=self.google_capability,
        ).normalized_rows[0]
        self.assertEqual(
            "12345678901234567890", row["account"]["platform_id"]
        )
        self.assertEqual(
            "34567890123456789012", row["ad_group"]["platform_id"]
        )
        self.assertEqual(
            "45678901234567890123", row["creative"]["platform_id"]
        )
        self.assertEqual("2026-07-01", row["reporting"]["start_date"])
        self.assertEqual("interaction_date", row["reporting"]["basis"])
        self.assertEqual(
            self.google_registration["segment_grain"],
            row["reporting"]["segment_grain"],
        )
        self.assertEqual("America/New_York", row["reporting"]["timezone"])
        self.assertEqual("mature", row["reporting"]["latency_state"])
        self.assertEqual(
            "rows_omitted_when_all_metrics_zero",
            row["outcome"]["omitted_zero_behavior"],
        )
        self.assertEqual("USD", row["currency"]["code"])

    def test_google_rejects_time_basis_or_segment_grain_substitution(self):
        for field, changed in (
            ("time_basis", "conversion_date"),
            ("segment_grain", ["customer.id", "segments.date"]),
        ):
            with self.subTest(field=field):
                payload = json.loads(json.dumps(self.google_fixture))
                payload["reporting_metadata"][field] = changed
                with self.assertRaisesRegex(AdapterError, field.replace("_", " ")):
                    self.google.normalize(
                        payload,
                        registration=self.google_registration,
                        capability=self.google_capability,
                    )

    def test_registration_and_metadata_cannot_drift_together_from_variant(self):
        meta_attacks = (
            ("request_level", "campaign"),
            ("time_increment", "all_days"),
        )
        for field, changed in meta_attacks:
            with self.subTest(platform="meta", field=field):
                payload = json.loads(json.dumps(self.meta_fixture))
                registration = dict(self.meta_registration)
                payload["reporting_metadata"][field] = changed
                registration[field] = changed
                with self.assertRaisesRegex(AdapterError, "exact.*variant"):
                    self.meta.normalize(
                        payload,
                        registration=registration,
                        capability=self.meta_capability,
                    )

        meta_range = json.loads(json.dumps(self.meta_fixture))
        meta_range["rows"][0]["date_stop"] = "2026-07-02"
        with self.assertRaisesRegex(AdapterError, "daily.*date"):
            self.meta.normalize(
                meta_range,
                registration=self.meta_registration,
                capability=self.meta_capability,
            )

        meta_basis = json.loads(json.dumps(self.meta_fixture))
        meta_basis["reporting_metadata"]["reporting_basis"] = (
            "campaign_reporting_window"
        )
        with self.assertRaisesRegex(AdapterError, "exact.*variant"):
            self.meta.normalize(
                meta_basis,
                registration=self.meta_registration,
                capability=self.meta_capability,
            )

        for field, changed in (
            ("time_basis", "conversion_date"),
            ("segment_grain", ["customer.id", "segments.date"]),
        ):
            with self.subTest(platform="google", field=field):
                payload = json.loads(json.dumps(self.google_fixture))
                registration = dict(self.google_registration)
                payload["reporting_metadata"][field] = changed
                registration[field] = changed
                with self.assertRaisesRegex(AdapterError, "exact.*variant"):
                    self.google.normalize(
                        payload,
                        registration=registration,
                        capability=self.google_capability,
                    )

        google_omission = json.loads(json.dumps(self.google_fixture))
        google_omission["reporting_metadata"]["omitted_zero_behavior"] = (
            "explicit_zero_rows"
        )
        with self.assertRaisesRegex(AdapterError, "exact.*variant"):
            self.google.normalize(
                google_omission,
                registration=self.google_registration,
                capability=self.google_capability,
            )

    def test_exact_api_rows_require_integral_counts_and_unique_references(self):
        for adapter, fixture, registration, capability, path in (
            (
                self.meta,
                self.meta_fixture,
                self.meta_registration,
                self.meta_capability,
                ("impressions",),
            ),
            (
                self.google,
                self.google_fixture,
                self.google_registration,
                self.google_capability,
                ("metrics", "clicks"),
            ),
        ):
            with self.subTest(adapter=adapter.adapter_id):
                payload = json.loads(json.dumps(fixture))
                target = payload["rows"][0]
                for key in path[:-1]:
                    target = target[key]
                target[path[-1]] = "1.5"
                with self.assertRaisesRegex(AdapterError, "integral count"):
                    adapter.normalize(
                        payload,
                        registration=registration,
                        capability=capability,
                    )

                duplicate = json.loads(json.dumps(fixture))
                duplicate["rows"].append(
                    json.loads(json.dumps(duplicate["rows"][0]))
                )
                with self.assertRaisesRegex(
                    AdapterError, "source_row_reference"
                ):
                    adapter.normalize(
                        duplicate,
                        registration=registration,
                        capability=capability,
                    )

    def test_zero_modeled_and_estimated_states_are_not_collapsed(self):
        google_zero = json.loads(json.dumps(self.google_fixture))
        google_zero["rows"][0]["metrics"]["conversions"] = "0"
        row = self.google.normalize(
            google_zero,
            registration=self.google_registration,
            capability=self.google_capability,
        ).normalized_rows[0]
        self.assertEqual("observed_zero", row["outcome"]["value_state"])
        self.assertEqual("0", row["outcome"]["source_numeric_text"])

        for state in ("modeled", "estimated"):
            with self.subTest(state=state):
                payload = json.loads(json.dumps(self.meta_fixture))
                payload["reporting_metadata"]["conversion_value_state"] = state
                row = self.meta.normalize(
                    payload,
                    registration=self.meta_registration,
                    capability=self.meta_capability,
                ).normalized_rows[0]
                self.assertEqual(state, row["outcome"]["value_state"])
                self.assertEqual("3.50", row["outcome"]["source_numeric_text"])

    def test_quality_state_is_orthogonal_to_latency_for_both_adapters(self):
        for adapter, fixture, registration, capability in (
            (
                self.meta,
                self.meta_fixture,
                self.meta_registration,
                self.meta_capability,
            ),
            (
                self.google,
                self.google_fixture,
                self.google_registration,
                self.google_capability,
            ),
        ):
            with self.subTest(adapter=adapter.adapter_id):
                payload = json.loads(json.dumps(fixture))
                payload["reporting_metadata"]["latency_state"] = "immature"
                payload["reporting_metadata"]["conversion_value_state"] = (
                    "modeled"
                )
                row = adapter.normalize(
                    payload,
                    registration=registration,
                    capability=capability,
                ).normalized_rows[0]
                self.assertEqual("immature", row["reporting"]["latency_state"])
                self.assertEqual("modeled", row["outcome"]["value_state"])

    def test_both_adapters_use_identical_observed_numeric_state_rules(self):
        meta_zero = json.loads(json.dumps(self.meta_fixture))
        meta_zero["rows"][0]["actions"][1]["value"] = "0"
        google_zero = json.loads(json.dumps(self.google_fixture))
        google_zero["rows"][0]["metrics"]["conversions"] = "0"
        for adapter, payload, registration, capability in (
            (
                self.meta,
                meta_zero,
                self.meta_registration,
                self.meta_capability,
            ),
            (
                self.google,
                google_zero,
                self.google_registration,
                self.google_capability,
            ),
        ):
            with self.subTest(adapter=adapter.adapter_id):
                row = adapter.normalize(
                    payload,
                    registration=registration,
                    capability=capability,
                ).normalized_rows[0]
                self.assertEqual("observed_zero", row["outcome"]["value_state"])

    def test_null_suppressed_or_non_numeric_is_never_coerced_to_zero(self):
        for value in (None, "<5", "--"):
            with self.subTest(value=value):
                payload = json.loads(json.dumps(self.google_fixture))
                payload["rows"][0]["metrics"]["conversions"] = value
                with self.assertRaisesRegex(AdapterError, "metrics.conversions"):
                    self.google.normalize(
                        payload,
                        registration=self.google_registration,
                        capability=self.google_capability,
                    )

    def test_concrete_adapters_refuse_other_exact_variants(self):
        with self.assertRaisesRegex(AdapterError, "exact adapter variant"):
            MetaInsightsAdapter(self.blocked_meta_capability)
        for adapter_type, capability, mutation in (
            (
                MetaInsightsAdapter,
                self.meta_capability,
                {"report_type": "insights_api_campaign_daily"},
            ),
            (
                GoogleAdsAdapter,
                self.google_capability,
                {"row_grain": ("customer_id", "date")},
            ),
        ):
            with self.subTest(adapter=adapter_type.__name__):
                with self.assertRaisesRegex(
                    AdapterError, "exact adapter variant"
                ):
                    adapter_type(replace(capability, **mutation))

    def test_both_adapters_emit_strict_self_hashed_rich_v1_rows(self):
        for adapter, fixture, registration, capability in (
            (
                self.meta,
                self.meta_fixture,
                self.meta_registration,
                self.meta_capability,
            ),
            (
                self.google,
                self.google_fixture,
                self.google_registration,
                self.google_capability,
            ),
        ):
            with self.subTest(adapter=adapter.adapter_id):
                row = adapter.normalize(
                    fixture,
                    registration=registration,
                    capability=capability,
                ).normalized_rows[0]
                self.assertRegex(
                    row["normalized_observation_sha256"],
                    r"^sha256:[0-9a-f]{64}$",
                )
                self.assertEqual(row, validate_normalized_observation(row))
                self.assertEqual(
                    row,
                    adapter.normalize(
                        fixture,
                        registration=registration,
                        capability=capability,
                    ).normalized_rows[0],
                )
                self.assertEqual(
                    {
                        "billed_currency": None,
                        "currency_relationship": "not_applicable",
                        "privacy_review_state": "not_applicable",
                        "demographic_truncation_state": "not_applicable",
                        "click_semantic": "not_applicable",
                        "optimization_event": None,
                        "delivery_state": "not_applicable",
                        "skan_state": "not_applicable",
                        "search_term_id": None,
                        "search_term_state": "not_applicable",
                    },
                    row["platform_semantics"],
                )

    def test_rich_v1_rejects_unknown_or_malformed_nested_values(self):
        row = self.meta.normalize(
            self.meta_fixture,
            registration=self.meta_registration,
            capability=self.meta_capability,
        ).normalized_rows[0]
        mutations = (
            (
                lambda value: value["outcome"].__setitem__(
                    "helpful_estimate", 4
                ),
                "unknown fields",
            ),
            (
                lambda value: value["exposure"]["impressions"].__setitem__(
                    "value", True
                ),
                "numeric",
            ),
            (
                lambda value: value["adapter"].__setitem__(
                    "maturity", "contract_ready"
                ),
                "maturity",
            ),
            (
                lambda value: value["outcome"].__setitem__(
                    "source_numeric_text", "3.51"
                ),
                "source_numeric_text",
            ),
        )
        for mutation, message in mutations:
            with self.subTest(message=message):
                changed = json.loads(json.dumps(row))
                mutation(changed)
                changed["normalized_observation_sha256"] = None
                with self.assertRaisesRegex(ContractError, message):
                    validate_normalized_observation(changed)


class LinkedInAndTikTokAdapterTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        registry = load_capability_registry(REGISTRY_PATH)
        cls.linkedin_capability = next(
            record
            for record in registry
            if record.adapter_id == "linkedin-ads-reporting-api-json-v1"
        )
        cls.blocked_linkedin_capability = next(
            record
            for record in registry
            if record.adapter_id == "linkedin-campaign-manager-csv-en-us-v1"
        )
        cls.tiktok_capability = next(
            record
            for record in registry
            if record.adapter_id == "tiktok-reporting-api-json-v1"
        )
        cls.blocked_tiktok_capability = next(
            record
            for record in registry
            if record.adapter_id == "tiktok-custom-report-csv-en-us-v1"
        )

    def setUp(self):
        self.linkedin = LinkedInAdsAdapter(self.linkedin_capability)
        self.tiktok = TikTokAdsAdapter(self.tiktok_capability)
        self.linkedin_registration = {
            "study_id": "study-1",
            "registration_id": "registration-1",
            "metric_id": "purchase",
            "conversion_source_field": "externalWebsiteConversions",
            "pivot": ["ACCOUNT", "CAMPAIGN", "CREATIVE"],
            "time_granularity": "DAILY",
            "attribution_windows": ["30d_click", "7d_view"],
        }
        self.linkedin_fixture = {
            "source_id": "source-linkedin-1",
            "import_id": "import-1",
            "source_sha256": "sha256:" + "c" * 64,
            "reporting_metadata": {
                "account_currency": "BRL",
                "billed_currency": "USD",
                "reporting_timezone": "UTC",
                "pivot": ["ACCOUNT", "CAMPAIGN", "CREATIVE"],
                "time_granularity": "DAILY",
                "attribution_windows": ["30d_click", "7d_view"],
                "privacy_state": "aggregate_privacy_reviewed",
                "demographic_truncation": "top_100_categories",
                "currency_state": "local_currency_distinct_from_billed_currency",
                "latency_state": "mature",
                "conversion_value_state": "modeled",
                "observed_at": "2026-07-10T12:00:00Z",
                "omitted_zero_behavior": "omitted_metrics_are_unknown_not_zero",
            },
            "rows": [
                {
                    "source_row_reference": "elements[0]",
                    "pivotValues": [
                        "urn:li:sponsoredAccount:101",
                        "urn:li:sponsoredCampaign:202",
                        "urn:li:sponsoredCreative:123",
                    ],
                    "dateRange": {
                        "start": "2026-07-01",
                        "end": "2026-07-01",
                    },
                    "impressions": "1000",
                    "clicks": "12",
                    "costInLocalCurrency": "123.4500",
                    "externalWebsiteConversions": "3.50",
                    "oneClickLeads": "2",
                }
            ],
        }
        self.tiktok_registration = {
            "study_id": "study-1",
            "registration_id": "registration-1",
            "metric_id": "purchase",
            "registered_source_metric": "conversion",
            "registered_click_metric": "destination_clicks",
            "optimization_event": "COMPLETE_PAYMENT",
            "attribution_windows": [
                "7d_click",
                "1d_engaged_view",
                "1d_view",
            ],
        }
        self.tiktok_fixture = {
            "source_id": "source-tiktok-1",
            "import_id": "import-1",
            "source_sha256": "sha256:" + "d" * 64,
            "reporting_metadata": {
                "advertiser_currency": "USD",
                "account_timezone": "America/Los_Angeles",
                "account_scope": "single_advertiser",
                "reporting_timezone": "America/Los_Angeles",
                "ad_id_field": "ad_id",
                "optimization_event": "COMPLETE_PAYMENT",
                "attribution_windows": [
                    "7d_click",
                    "1d_engaged_view",
                    "1d_view",
                ],
                "omitted_zero_behavior": "omitted_metrics_are_unknown_not_zero",
                "latency_state": "mature",
                "delivery_state": "standard",
                "spend_value_state": "estimated",
                "observed_at": "2026-07-10T12:00:00-07:00",
            },
            "rows": [
                {
                    "source_row_reference": "list[0]",
                    "advertiser_id": "7100000000000000001",
                    "campaign_id": "7100000000000000002",
                    "adgroup_id": "7100000000000000003",
                    "ad_id": "7100000000000000004",
                    "stat_time_day": "2026-07-01",
                    "impressions": "1000",
                    "clicks": "25",
                    "destination_clicks": "12",
                    "spend": "45.6700",
                    "conversion": "2.5",
                    "real_time_conversion": "3",
                    "search_term_id": "-1",
                }
            ],
        }

    def test_linkedin_preserves_full_urn_and_requires_conversion_source(self):
        result = self.linkedin.normalize(
            self.linkedin_fixture,
            registration=self.linkedin_registration,
            capability=self.linkedin_capability,
        )
        row = result.normalized_rows[0]
        self.assertEqual(
            "urn:li:sponsoredAccount:101",
            row["account"]["platform_id"],
        )
        self.assertEqual(
            "urn:li:sponsoredCreative:123",
            row["creative"]["platform_id"],
        )
        self.assertEqual(
            "not_applicable",
            row["ad_group"]["platform_id"],
        )
        self.assertEqual(
            "externalWebsiteConversions",
            row["outcome"]["source_metric"],
        )
        missing = dict(self.linkedin_registration)
        missing.pop("conversion_source_field")
        with self.assertRaisesRegex(
            AdapterError, "conversion_source_field is required"
        ):
            self.linkedin.normalize(
                self.linkedin_fixture,
                registration=missing,
                capability=self.linkedin_capability,
            )

    def test_linkedin_never_substitutes_another_objective_metric(self):
        payload = json.loads(json.dumps(self.linkedin_fixture))
        del payload["rows"][0]["externalWebsiteConversions"]
        with self.assertRaisesRegex(
            AdapterError, "configured LinkedIn conversion field is absent"
        ):
            self.linkedin.normalize(
                payload,
                registration=self.linkedin_registration,
                capability=self.linkedin_capability,
            )
        registration = dict(self.linkedin_registration)
        registration["conversion_source_field"] = "oneClickLeads"
        row = self.linkedin.normalize(
            self.linkedin_fixture,
            registration=registration,
            capability=self.linkedin_capability,
        ).normalized_rows[0]
        self.assertEqual("oneClickLeads", row["outcome"]["source_metric"])
        self.assertEqual("2", row["outcome"]["source_numeric_text"])

    def test_linkedin_validates_every_present_admitted_metric_before_selection(self):
        for value in ("malformed", "-1", "Infinity"):
            with self.subTest(value=value):
                payload = json.loads(json.dumps(self.linkedin_fixture))
                payload["rows"][0]["oneClickLeads"] = value
                with self.assertRaisesRegex(AdapterError, "oneClickLeads"):
                    self.linkedin.normalize(
                        payload,
                        registration=self.linkedin_registration,
                        capability=self.linkedin_capability,
                    )

    def test_linkedin_api_counts_and_conversions_may_be_json_numbers(self):
        payload = json.loads(json.dumps(self.linkedin_fixture))
        payload["rows"][0]["impressions"] = 1000
        payload["rows"][0]["clicks"] = 12
        payload["rows"][0]["externalWebsiteConversions"] = 3.5
        payload["rows"][0]["oneClickLeads"] = 2
        row = self.linkedin.normalize(
            payload,
            registration=self.linkedin_registration,
            capability=self.linkedin_capability,
        ).normalized_rows[0]
        self.assertEqual(
            "1000",
            row["exposure"]["impressions"]["source_numeric_text"],
        )
        self.assertEqual("3.5", row["outcome"]["source_numeric_text"])
        self.assertEqual("123.4500", row["spend"]["source_numeric_text"])

    def test_linkedin_privacy_and_access_states_are_never_zero(self):
        for source_value, expected_state in (
            ("<5", "suppressed"),
            ("NO_ACCESS", "absent"),
            ("", "null"),
            (None, "null"),
        ):
            with self.subTest(source_value=source_value):
                payload = json.loads(json.dumps(self.linkedin_fixture))
                payload["rows"][0][
                    "externalWebsiteConversions"
                ] = source_value
                row = self.linkedin.normalize(
                    payload,
                    registration=self.linkedin_registration,
                    capability=self.linkedin_capability,
                ).normalized_rows[0]
                self.assertEqual(expected_state, row["outcome"]["value_state"])
                self.assertIsNone(row["outcome"]["value"])
                self.assertIsNone(row["outcome"]["source_numeric_text"])

    def test_linkedin_preserves_decimal_modeled_and_brl_currency_state(self):
        result = self.linkedin.normalize(
            self.linkedin_fixture,
            registration=self.linkedin_registration,
            capability=self.linkedin_capability,
        )
        row = result.normalized_rows[0]
        self.assertEqual("123.4500", row["spend"]["decimal"])
        self.assertEqual(
            "123.4500", row["spend"]["source_numeric_text"]
        )
        self.assertEqual("modeled", row["outcome"]["value_state"])
        self.assertEqual("3.50", row["outcome"]["source_numeric_text"])
        self.assertEqual("BRL", row["currency"]["code"])
        self.assertEqual(
            "local_currency_units", row["spend"]["source_unit"]
        )
        self.assertEqual(
            "USD",
            result.mapping_report["source_semantics"]["billed_currency"],
        )
        self.assertEqual(
            "local_currency_distinct_from_billed_currency",
            result.mapping_report["source_semantics"]["currency_state"],
        )
        self.assertEqual(
            "USD", row["platform_semantics"]["billed_currency"]
        )
        self.assertEqual(
            "local_currency_distinct_from_billed_currency",
            row["platform_semantics"]["currency_relationship"],
        )

    def test_linkedin_pins_utc_pivot_and_daily_versus_period_grain(self):
        row = self.linkedin.normalize(
            self.linkedin_fixture,
            registration=self.linkedin_registration,
            capability=self.linkedin_capability,
        ).normalized_rows[0]
        self.assertEqual("UTC", row["reporting"]["timezone"])
        self.assertEqual("utc_reporting_day", row["reporting"]["basis"])
        self.assertEqual("1", row["reporting"]["time_increment"])

        period_payload = json.loads(json.dumps(self.linkedin_fixture))
        period_registration = dict(self.linkedin_registration)
        period_payload["reporting_metadata"]["time_granularity"] = "PERIOD"
        period_registration["time_granularity"] = "PERIOD"
        period_payload["rows"][0]["dateRange"]["end"] = "2026-07-07"
        period = self.linkedin.normalize(
            period_payload,
            registration=period_registration,
            capability=self.linkedin_capability,
        ).normalized_rows[0]
        self.assertEqual("utc_reporting_period", period["reporting"]["basis"])
        self.assertEqual("period", period["reporting"]["time_increment"])

        for field, changed in (
            ("reporting_timezone", "America/Sao_Paulo"),
            ("pivot", ["CAMPAIGN", "CREATIVE", "ACCOUNT"]),
        ):
            with self.subTest(field=field):
                payload = json.loads(json.dumps(self.linkedin_fixture))
                registration = dict(self.linkedin_registration)
                payload["reporting_metadata"][field] = changed
                if field == "pivot":
                    registration[field] = changed
                with self.assertRaisesRegex(AdapterError, "exact.*variant"):
                    self.linkedin.normalize(
                        payload,
                        registration=registration,
                        capability=self.linkedin_capability,
                    )

    def test_linkedin_preserves_privacy_and_top_100_context(self):
        result = self.linkedin.normalize(
            self.linkedin_fixture,
            registration=self.linkedin_registration,
            capability=self.linkedin_capability,
        )
        semantics = result.mapping_report["source_semantics"]
        self.assertEqual(
            "aggregate_privacy_reviewed", semantics["privacy_state"]
        )
        self.assertEqual(
            "top_100_categories", semantics["demographic_truncation"]
        )
        row_semantics = result.normalized_rows[0]["platform_semantics"]
        self.assertEqual(
            "aggregate_privacy_reviewed",
            row_semantics["privacy_review_state"],
        )
        self.assertEqual(
            "top_100_categories",
            row_semantics["demographic_truncation_state"],
        )

    def test_tiktok_suppressed_value_is_not_zero(self):
        payload = json.loads(json.dumps(self.tiktok_fixture))
        payload["rows"][0]["conversion"] = "<5"
        result = self.tiktok.normalize(
            payload,
            registration=self.tiktok_registration,
            capability=self.tiktok_capability,
        )
        row = result.normalized_rows[0]
        self.assertEqual("suppressed", row["outcome"]["value_state"])
        self.assertIsNone(row["outcome"]["value"])
        self.assertIsNone(row["outcome"]["source_numeric_text"])

    def test_tiktok_conversion_time_basis_cannot_be_swapped(self):
        realtime_only = json.loads(json.dumps(self.tiktok_fixture))
        del realtime_only["rows"][0]["conversion"]
        with self.assertRaisesRegex(AdapterError, "time basis"):
            self.tiktok.normalize(
                realtime_only,
                registration=self.tiktok_registration,
                capability=self.tiktok_capability,
            )

        registration = dict(self.tiktok_registration)
        registration["registered_source_metric"] = "real_time_conversion"
        row = self.tiktok.normalize(
            self.tiktok_fixture,
            registration=registration,
            capability=self.tiktok_capability,
        ).normalized_rows[0]
        self.assertEqual("conversion_time", row["attribution"]["report_time"])
        self.assertEqual(
            "real_time_conversion", row["outcome"]["source_metric"]
        )

    def test_tiktok_validates_every_present_admitted_metric_before_selection(self):
        for field, value in (
            ("real_time_conversion", "bad"),
            ("destination_clicks", "1.5"),
            ("spend", "-1"),
        ):
            with self.subTest(field=field):
                payload = json.loads(json.dumps(self.tiktok_fixture))
                payload["rows"][0][field] = value
                with self.assertRaisesRegex(AdapterError, field):
                    self.tiktok.normalize(
                        payload,
                        registration=self.tiktok_registration,
                        capability=self.tiktok_capability,
                    )

    def test_tiktok_preserves_identity_lanes_numeric_text_and_click_semantics(self):
        result = self.tiktok.normalize(
            self.tiktok_fixture,
            registration=self.tiktok_registration,
            capability=self.tiktok_capability,
        )
        row = result.normalized_rows[0]
        self.assertEqual(
            "7100000000000000001", row["account"]["platform_id"]
        )
        self.assertEqual(
            "7100000000000000004", row["creative"]["platform_id"]
        )
        self.assertEqual("2026-07-01", row["reporting"]["start_date"])
        self.assertEqual(
            "12", row["exposure"]["clicks"]["source_numeric_text"]
        )
        self.assertEqual("45.6700", row["spend"]["source_numeric_text"])
        self.assertEqual("2.5", row["outcome"]["source_numeric_text"])
        self.assertEqual(
            "destination_clicks",
            result.mapping_report["source_semantics"]["click_metric"],
        )
        self.assertEqual(
            "destination_clicks",
            row["platform_semantics"]["click_semantic"],
        )
        self.assertEqual(
            "COMPLETE_PAYMENT",
            result.mapping_report["source_semantics"]["optimization_event"],
        )
        self.assertEqual(
            "COMPLETE_PAYMENT",
            row["platform_semantics"]["optimization_event"],
        )
        self.assertEqual(
            ["7d_click", "1d_engaged_view", "1d_view"],
            row["attribution"]["windows"],
        )

        v2 = json.loads(json.dumps(self.tiktok_fixture))
        v2["reporting_metadata"]["ad_id_field"] = "ad_id_v2"
        v2["rows"][0]["ad_id_v2"] = v2["rows"][0].pop("ad_id")
        result = self.tiktok.normalize(
            v2,
            registration=self.tiktok_registration,
            capability=self.tiktok_capability,
        )
        self.assertEqual(
            "7100000000000000004",
            result.normalized_rows[0]["creative"]["platform_id"],
        )
        self.assertEqual(
            "ad_id_v2",
            result.mapping_report["source_semantics"]["ad_id_field"],
        )

    def test_tiktok_all_clicks_uses_the_closed_all_clicks_semantic(self):
        registration = dict(self.tiktok_registration)
        registration["registered_click_metric"] = "clicks"
        row = self.tiktok.normalize(
            self.tiktok_fixture,
            registration=registration,
            capability=self.tiktok_capability,
        ).normalized_rows[0]
        self.assertEqual(
            "all_clicks",
            row["platform_semantics"]["click_semantic"],
        )
        self.assertEqual(
            "25", row["exposure"]["clicks"]["source_numeric_text"]
        )

    def test_tiktok_pins_single_account_timezone_and_multi_account_utc(self):
        single = self.tiktok.normalize(
            self.tiktok_fixture,
            registration=self.tiktok_registration,
            capability=self.tiktok_capability,
        ).normalized_rows[0]
        self.assertEqual(
            "America/Los_Angeles", single["reporting"]["timezone"]
        )
        self.assertEqual(
            "advertiser_reporting_day", single["reporting"]["basis"]
        )

        multi = json.loads(json.dumps(self.tiktok_fixture))
        multi["reporting_metadata"]["account_scope"] = "multi_advertiser"
        multi["reporting_metadata"]["reporting_timezone"] = "UTC"
        result = self.tiktok.normalize(
            multi,
            registration=self.tiktok_registration,
            capability=self.tiktok_capability,
        )
        row = result.normalized_rows[0]
        self.assertEqual("UTC", row["reporting"]["timezone"])
        self.assertEqual(
            "multi_advertiser_utc_day", row["reporting"]["basis"]
        )

        drift = json.loads(json.dumps(self.tiktok_fixture))
        drift["reporting_metadata"]["reporting_timezone"] = "UTC"
        with self.assertRaisesRegex(AdapterError, "timezone"):
            self.tiktok.normalize(
                drift,
                registration=self.tiktok_registration,
                capability=self.tiktok_capability,
            )

    def test_tiktok_preserves_unknown_search_skan_and_estimated_spend_states(self):
        skan = json.loads(json.dumps(self.tiktok_fixture))
        skan["reporting_metadata"]["latency_state"] = "immature"
        skan["reporting_metadata"]["delivery_state"] = "skan_delayed"
        result = self.tiktok.normalize(
            skan,
            registration=self.tiktok_registration,
            capability=self.tiktok_capability,
        )
        row = result.normalized_rows[0]
        self.assertEqual("immature", row["reporting"]["latency_state"])
        self.assertEqual(
            "estimated_advertiser_currency",
            row["spend"]["source_unit"],
        )
        semantics = result.mapping_report["source_semantics"]
        self.assertEqual("unknown", semantics["search_term_state"])
        self.assertEqual("skan_delayed", semantics["delivery_state"])
        self.assertEqual("estimated", semantics["spend_value_state"])
        row_semantics = row["platform_semantics"]
        self.assertEqual("delayed", row_semantics["delivery_state"])
        self.assertEqual("skan_delayed", row_semantics["skan_state"])
        self.assertEqual("-1", row_semantics["search_term_id"])
        self.assertEqual("unknown", row_semantics["search_term_state"])

    def test_tiktok_search_term_state_remains_bound_to_each_row(self):
        payload = json.loads(json.dumps(self.tiktok_fixture))
        second = json.loads(json.dumps(payload["rows"][0]))
        second["source_row_reference"] = "list[1]"
        second["ad_id"] = "7100000000000000005"
        second["search_term_id"] = "998877"
        payload["rows"].append(second)
        rows = self.tiktok.normalize(
            payload,
            registration=self.tiktok_registration,
            capability=self.tiktok_capability,
        ).normalized_rows
        self.assertEqual(
            ("-1", "unknown"),
            (
                rows[0]["platform_semantics"]["search_term_id"],
                rows[0]["platform_semantics"]["search_term_state"],
            ),
        )
        self.assertEqual(
            ("998877", "observed"),
            (
                rows[1]["platform_semantics"]["search_term_id"],
                rows[1]["platform_semantics"]["search_term_state"],
            ),
        )

    def test_tiktok_missing_unknown_or_suppressed_is_never_zero(self):
        for source_value, expected_state in (
            (None, "null"),
            ("", "null"),
            ("<5", "suppressed"),
        ):
            with self.subTest(source_value=source_value):
                payload = json.loads(json.dumps(self.tiktok_fixture))
                payload["rows"][0]["conversion"] = source_value
                row = self.tiktok.normalize(
                    payload,
                    registration=self.tiktok_registration,
                    capability=self.tiktok_capability,
                ).normalized_rows[0]
                self.assertEqual(expected_state, row["outcome"]["value_state"])
                self.assertIsNone(row["outcome"]["value"])

    def test_tiktok_pins_variant_semantics_independently_of_registration(self):
        payload = json.loads(json.dumps(self.tiktok_fixture))
        registration = dict(self.tiktok_registration)
        payload["reporting_metadata"]["optimization_event"] = "LEAD"
        with self.assertRaisesRegex(AdapterError, "optimization event"):
            self.tiktok.normalize(
                payload,
                registration=registration,
                capability=self.tiktok_capability,
            )
        registration["registered_source_metric"] = "complete_payment"
        with self.assertRaisesRegex(AdapterError, "source metric"):
            self.tiktok.normalize(
                self.tiktok_fixture,
                registration=registration,
                capability=self.tiktok_capability,
            )

    def test_linkedin_and_tiktok_refuse_other_or_forged_variants(self):
        for adapter_type, capability in (
            (LinkedInAdsAdapter, self.blocked_linkedin_capability),
            (TikTokAdsAdapter, self.blocked_tiktok_capability),
        ):
            with self.subTest(adapter=adapter_type.__name__):
                with self.assertRaisesRegex(
                    AdapterError, "exact adapter variant"
                ):
                    adapter_type(capability)
        for adapter_type, capability, mutation in (
            (
                LinkedInAdsAdapter,
                self.linkedin_capability,
                {"row_grain": ("campaign", "creative")},
            ),
            (
                TikTokAdsAdapter,
                self.tiktok_capability,
                {"time_basis": "conversion_time"},
            ),
        ):
            with self.subTest(adapter=adapter_type.__name__):
                with self.assertRaisesRegex(
                    AdapterError, "exact adapter variant"
                ):
                    adapter_type(replace(capability, **mutation))

    def test_linkedin_and_tiktok_emit_only_strict_rich_v1_rows(self):
        for adapter, fixture, registration, capability in (
            (
                self.linkedin,
                self.linkedin_fixture,
                self.linkedin_registration,
                self.linkedin_capability,
            ),
            (
                self.tiktok,
                self.tiktok_fixture,
                self.tiktok_registration,
                self.tiktok_capability,
            ),
        ):
            with self.subTest(adapter=adapter.adapter_id):
                result = adapter.normalize(
                    fixture,
                    registration=registration,
                    capability=capability,
                )
                row = result.normalized_rows[0]
                self.assertEqual(row, validate_normalized_observation(row))
                self.assertEqual("schema_tested", result.maturity)
                self.assertFalse(result.mapping_report["contract_ready"])
                self.assertEqual(
                    "incomplete",
                    result.mapping_report["operational_status"],
                )


class ProgrammaticAdapterGuardTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.registry = load_capability_registry(REGISTRY_PATH)

    def capability(self, adapter_id: str) -> AdapterCapability:
        return next(
            record
            for record in self.registry
            if record.adapter_id == adapter_id
        )

    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary_directory.cleanup)
        self.generic_base = Path(self.temporary_directory.name)
        self.generic_source_index = 0
        self.registration = {
            "study_id": "study-1",
            "registration_id": "registration-1",
            "metric_id": "purchase",
            "registered_source_metric": "Total Conversions",
            "attribution_windows": ["30d_click", "30d_view"],
            "cost_basis": "advertiser_currency",
        }
        self.dv360_capability = self.capability(
            "dv360-bid-manager-v2-standard-csv-v1"
        )
        self.dv360 = DV360Adapter(self.dv360_capability)
        self.dv360_fixture = {
            "source_id": "source-dv360-1",
            "import_id": "import-1",
            "source_sha256": "sha256:" + "e" * 64,
            "reporting_metadata": {
                "query_id": "query-1",
                "report_type": "STANDARD",
                "timezone_basis": "advertiser",
                "reporting_timezone": "America/New_York",
                "currency_code": "USD",
                "cost_basis": "advertiser_currency",
                "attribution_windows": ["30d_click", "30d_view"],
                "conversion_value_state": "observed",
                "latency_state": "mature",
                "observed_at": "2026-07-10T12:00:00-04:00",
                "omitted_zero_behavior": (
                    "omitted_metrics_are_unknown_not_zero"
                ),
                "mutability_days": 31,
                "dimension_metric_compatible": True,
            },
            "rows": [
                {
                    "source_row_reference": "row-2",
                    "Advertiser ID": "advertiser-1",
                    "Campaign ID": "campaign-1",
                    "Insertion Order ID": "io-1",
                    "Line Item ID": "line-1",
                    "Creative ID": "creative-1",
                    "Date": "2026-07-01",
                    "Impressions": "1000",
                    "Clicks": "12",
                    "Media Cost (Advertiser Currency)": "123.4500",
                    "Total Conversions": "2.5",
                }
            ],
        }

        self.ttd_capability = self.capability(
            "trade-desk-report-template-csv-v1"
        )
        self.ttd = TradeDeskAdapter(self.ttd_capability)
        self.ttd_registration = {
            **self.registration,
            "registered_source_metric": "Conversions",
        }
        self.ttd_fixture = {
            "source_id": "source-ttd-1",
            "import_id": "import-1",
            "source_sha256": "sha256:" + "f" * 64,
            "reporting_metadata": {
                "template_id": "template-1",
                "schedule_id": "schedule-1",
                "file_format": "csv",
                "date_format": "yyyy-MM-dd",
                "numeric_format": "decimal_point_no_grouping",
                "schedule_timezone": "America/Chicago",
                "reporting_timezone": "UTC",
                "report_start_date": "2026-07-01",
                "report_end_exclusive": "2026-07-02",
                "completed_report_state": "immutable_completed",
                "late_offline_conversion_state": "mature",
                "release_note_version": "2026-07",
                "schema_version": "report-template-v1",
                "advertiser_currency": "USD",
                "attribution_windows": ["30d_click", "30d_view"],
                "conversion_value_state": "observed",
                "observed_at": "2026-07-10T12:00:00Z",
                "omitted_zero_behavior": (
                    "omitted_metrics_are_unknown_not_zero"
                ),
            },
            "rows": [
                {
                    "source_row_reference": "row-2",
                    "AdvertiserId": "advertiser-1",
                    "CampaignId": "campaign-1",
                    "AdGroupId": "ad-group-1",
                    "CreativeId": "creative-1",
                    "Date": "2026-07-01",
                    "Impressions": "1000",
                    "Clicks": "12",
                    "AdvertiserCost": "123.4500",
                    "Conversions": "2",
                }
            ],
        }

        self.xandr_capability = self.capability(
            "xandr-advertiser-analytics-csv-v1"
        )
        self.xandr = XandrAdapter(self.xandr_capability)
        self.xandr_registration = {
            **self.registration,
            "registered_source_metric": "post_click_convs",
        }
        self.xandr_fixture = {
            "source_id": "source-xandr-1",
            "import_id": "import-1",
            "source_sha256": "sha256:" + "1" * 64,
            "reporting_metadata": {
                "advertiser_currency": "USD",
                "decimal_mark": ".",
                "report_start_date": "2026-07-01",
                "report_end_exclusive": "2026-07-02",
                "report_mode": "historical",
                "reporting_timezone": "UTC",
                "click_window": "30d_click",
                "view_window": "30d_view",
                "conversion_latency_state": "mature",
                "conversion_value_state": "observed",
                "observed_at": "2026-07-10T12:00:00Z",
                "omitted_zero_behavior": (
                    "omitted_metrics_are_unknown_not_zero"
                ),
            },
            "rows": [
                {
                    "source_row_reference": "row-2",
                    "advertiser_id": "advertiser-1",
                    "campaign_id": "campaign-1",
                    "insertion_order_id": "io-1",
                    "line_item_id": "line-1",
                    "creative_id": "0",
                    "day": "2026-07-01",
                    "imps": "1000",
                    "clicks": "12",
                    "media_cost": "123.4500",
                    "post_click_convs": "2",
                    "post_view_convs": "3",
                }
            ],
        }

        self.generic_capability = self.capability("generic-dsp-mapping-v1")
        self.generic = GenericProgrammaticAdapter(self.generic_capability)
        headers = [
            "Campaign Key",
            "Line Key",
            "Ad Key",
            "Creative Key",
            "Report Day",
            "Delivered",
            "Click Count",
            "Media Spend",
            "Currency Code",
            "Approved Purchase",
        ]
        mapping = {
            "Campaign Key": "campaign_id",
            "Line Key": "line_item_id",
            "Ad Key": "ad_id",
            "Creative Key": "creative_id",
            "Report Day": "date",
            "Delivered": "impressions",
            "Click Count": "clicks",
            "Media Spend": "spend",
            "Currency Code": "currency",
            "Approved Purchase": "conversion_value",
        }
        self.generic_registration = {
            "study_id": "study-1",
            "registration_id": "registration-1",
            "metric_id": "purchase",
            "registered_source_metric": "Approved Purchase",
            "outcomes_accessed": True,
            "sealed_delivery_map": {
                "schema_version": "outcome-delivery-map-v1",
                "study_id": "study-1",
                "registration_id": "registration-1",
                "sealed_before_outcome_access": True,
                "mappings": [
                    {
                        "mapping_id": "mapping-1",
                        "platform": "generic_dsp",
                        "platform_campaign_id": "campaign-1",
                        "platform_ad_group_id": "line-1",
                        "platform_ad_id": "ad-1",
                        "platform_creative_id": "creative-1",
                        "block_id": "block-1",
                        "study_id": "study-1",
                        "arm_id": "arm-1",
                        "batch_id": "batch-1",
                        "segment_ids": ["segment-1"],
                        "creative_id": "creative-1",
                        "variant_id": "variant-1",
                        "asset_sha256": "sha256:" + "3" * 64,
                        "campaign_plan_sha256": "sha256:" + "4" * 64,
                    }
                ],
                "chronology": {
                    "events": [
                        {
                            "event_type": "delivery_map_sealed",
                            "occurred_at": "2026-06-30T12:00:00Z",
                            "evidence_source_sha256": "sha256:" + "5" * 64,
                            "attested_by": "operator-1",
                            "attested_at": "2026-06-30T12:00:00Z",
                            "authority_id": "operator-1",
                        }
                    ]
                },
                "delivery_map_sha256": None,
            },
            "approved_mapping": mapping,
            "approved_mapping_profile_id": "mapping-profile-1",
            "approved_header_fingerprint": sha256_json(sorted(headers)),
            "approved_source_container": "tsv",
            "time_basis": "advertiser_reporting_day",
            "currency": "USD",
            "attribution_semantics": "platform_attribution",
            "attribution_windows": ["30d_click", "30d_view"],
        }
        self.generic_fixture = {
            "source_id": "source-generic-1",
            "import_id": "import-1",
            "source_sha256": "sha256:" + "2" * 64,
            "mapping": mapping,
            "reporting_metadata": {
                "source_container": "tsv",
                "source_platform": "generic_dsp",
                "headers": headers,
                "header_fingerprint": sha256_json(sorted(headers)),
                "mapping_profile_id": "mapping-profile-1",
                "stable_id_targets": [
                    "campaign_id",
                    "line_item_id",
                    "ad_id",
                    "creative_id",
                ],
                "timezone": "America/New_York",
                "time_basis": "advertiser_reporting_day",
                "currency": "USD",
                "attribution_semantics": "platform_attribution",
                "attribution_windows": ["30d_click", "30d_view"],
                "conversion_metric": "Approved Purchase",
                "admitted_null_tokens": ["NA"],
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
                "observed_at": "2026-07-10T12:00:00-04:00",
                "omitted_zero_behavior": (
                    "omitted_metrics_are_unknown_not_zero"
                ),
            },
            "rows": [
                {
                    "source_row_reference": "row-2",
                    "values": {
                        "Campaign Key": "campaign-1",
                        "Line Key": "line-1",
                        "Ad Key": "ad-1",
                        "Creative Key": "creative-1",
                        "Report Day": "2026-07-01",
                        "Delivered": "1000",
                        "Click Count": "12",
                        "Media Spend": "123.4500",
                        "Currency Code": "USD",
                        "Approved Purchase": "2.5",
                    },
                }
            ],
        }

    def test_ttd_requires_template_and_schedule_identity(self):
        with self.assertRaisesRegex(AdapterError, "template_id"):
            require_ttd_report_identity({"schedule_id": "schedule-1"})

    def test_dv360_requires_query_report_and_timezone_context(self):
        with self.assertRaisesRegex(AdapterError, "timezone_basis"):
            require_dv360_report_context(
                {
                    "query_id": "query-1",
                    "report_type": "STANDARD",
                },
                allowed_report_types={"STANDARD"},
            )

    def test_amazon_initial_variants_remain_unavailable(self):
        capability = self.capability(
            "amazon-unified-reporting-api-json-v1"
        )
        with self.assertRaisesRegex(
            AdapterError, "blocked pending a sanitized export"
        ):
            amazon_variant_available(capability)
        with self.assertRaisesRegex(
            AdapterError, "blocked pending a sanitized export"
        ):
            AmazonDSPAdapter(capability).normalize(
                {},
                registration={},
                capability=capability,
            )

    def test_amazon_blocked_metadata_excludes_product_sales_values(self):
        expected_metrics = {
            "amazon-unified-reporting-ui-csv-v1": ("Purchases",),
            "amazon-unified-reporting-ui-xlsx-v1": ("Purchases",),
            "amazon-unified-reporting-api-json-v1": ("purchases",),
        }
        for adapter_id, metric_fields in expected_metrics.items():
            capability = self.capability(adapter_id)
            with self.subTest(adapter_id=adapter_id):
                self.assertEqual(metric_fields, capability.metric_fields)
                self.assertTrue(
                    any(
                        field in capability.identity_fields
                        for field in ("Order ID", "Order", "orderId")
                    )
                )
                AmazonDSPAdapter(capability)

    def test_xandr_external_clicks_is_not_a_creative_id(self):
        self.assertEqual(("external_tracker", None), xandr_creative_state("0"))
        self.assertEqual(("external_tracker", None), xandr_creative_state("-1"))
        self.assertEqual(
            ("platform_creative", "creative-1"),
            xandr_creative_state("creative-1"),
        )

    def test_generic_mapper_cannot_authorize_post_outcome_identity(self):
        with self.assertRaisesRegex(AdapterError, "sealed delivery map"):
            validate_generic_mapping(
                {
                    "Campaign": "campaign_id",
                    "Creative": "creative_id",
                },
                sealed_delivery_map={
                    "sealed_before_outcome_access": False,
                },
                outcomes_accessed=True,
            )

    def test_dv360_preserves_full_identity_context_and_rich_row(self):
        result = self.dv360.normalize(
            self.dv360_fixture,
            registration=self.registration,
            capability=self.dv360_capability,
        )
        row = result.normalized_rows[0]
        self.assertEqual(row, validate_normalized_observation(row))
        self.assertEqual("2.5", row["outcome"]["source_numeric_text"])
        self.assertEqual("line-1", row["ad_group"]["platform_id"])
        self.assertEqual(
            "io-1",
            result.mapping_report["source_semantics"][
                "row_identity_context"
            ][0]["insertion_order_id"],
        )
        self.assertFalse(result.mapping_report["contract_ready"])

    def test_dv360_exact_variants_reject_other_cost_bases(self):
        for cost_basis in ("partner_currency", "usd"):
            with self.subTest(cost_basis=cost_basis):
                payload = json.loads(json.dumps(self.dv360_fixture))
                payload["reporting_metadata"]["cost_basis"] = cost_basis
                with self.assertRaisesRegex(
                    AdapterError, "advertiser-currency capability"
                ):
                    self.dv360.normalize(
                        payload,
                        registration=self.registration,
                        capability=self.dv360_capability,
                    )

    def test_programmatic_adapters_validate_unselected_metrics(self):
        for adapter, fixture, registration, capability, field in ((
            self.xandr,
            self.xandr_fixture,
            self.xandr_registration,
            self.xandr_capability,
            "post_view_convs",
        ),):
            with self.subTest(adapter=adapter.adapter_id):
                payload = json.loads(json.dumps(fixture))
                payload["rows"][0][field] = "not-numeric"
                with self.assertRaisesRegex(AdapterError, re.escape(field)):
                    adapter.normalize(
                        payload,
                        registration=registration,
                        capability=capability,
                    )

    def test_named_programmatic_admission_rejects_business_data_headers(self):
        for adapter, capability, prohibited_header in (
            (self.dv360, self.dv360_capability, "CRM Revenue"),
            (
                self.ttd,
                self.ttd_capability,
                "Customer Lifetime Value",
            ),
        ):
            headers = tuple(
                capability.identity_fields
                + capability.required_fields
                + capability.metric_fields
                + (prohibited_header,)
            )
            source = inventory(
                media_type="text/csv",
                headers=headers,
                rows=(tuple("100" for _ in headers),),
            )
            with self.subTest(adapter=adapter.adapter_id):
                classified = adapter.inventory(
                    source,
                    capability,
                    require_exact_schema=False,
                )
                validation = adapter.validate(
                    classified,
                    registration={},
                    governance={"minimum_group_size_rule": "0"},
                    capability=capability,
                )
                self.assertIn("prohibited_business_data", validation.errors)

    def test_named_programmatic_normalizers_reject_business_outcomes(self):
        for adapter, fixture, registration, capability, field in (
            (
                self.dv360,
                self.dv360_fixture,
                self.registration,
                self.dv360_capability,
                "Total Revenue (Advertiser Currency)",
            ),
            (
                self.ttd,
                self.ttd_fixture,
                self.ttd_registration,
                self.ttd_capability,
                "Revenue",
            ),
        ):
            with self.subTest(adapter=adapter.adapter_id, use="source_presence"):
                prohibited_source = json.loads(json.dumps(fixture))
                prohibited_source["rows"][0][field] = "500.00"
                with self.assertRaisesRegex(AdapterError, "business data"):
                    adapter.normalize(
                        prohibited_source,
                        registration=registration,
                        capability=capability,
                    )
            selected = dict(registration)
            selected["registered_source_metric"] = field
            with self.subTest(adapter=adapter.adapter_id, use="selected_metric"):
                with self.assertRaisesRegex(AdapterError, "business data"):
                    adapter.normalize(
                        fixture,
                        registration=selected,
                        capability=capability,
                    )

    def test_ttd_preserves_report_identity_and_exclusive_boundary(self):
        result = self.ttd.normalize(
            self.ttd_fixture,
            registration=self.ttd_registration,
            capability=self.ttd_capability,
        )
        row = result.normalized_rows[0]
        self.assertEqual(row, validate_normalized_observation(row))
        semantics = result.mapping_report["source_semantics"]
        self.assertEqual("template-1", semantics["template_id"])
        self.assertEqual("schedule-1", semantics["schedule_id"])
        self.assertEqual("2026-07-02", semantics["report_end_exclusive"])

    def test_xandr_sentinel_is_quarantined_after_metric_validation(self):
        result = self.xandr.normalize(
            self.xandr_fixture,
            registration=self.xandr_registration,
            capability=self.xandr_capability,
        )
        self.assertEqual((), result.normalized_rows)
        self.assertEqual(
            "external_tracker",
            result.quarantined_rows[0]["creative_id_state"],
        )
        self.assertEqual(
            "io-1",
            result.mapping_report["source_semantics"][
                "row_identity_context"
            ][0]["insertion_order_id"],
        )

    def test_xandr_preserves_insertion_order_and_strict_rich_row(self):
        payload = json.loads(json.dumps(self.xandr_fixture))
        payload["rows"][0]["creative_id"] = "creative-1"
        result = self.xandr.normalize(
            payload,
            registration=self.xandr_registration,
            capability=self.xandr_capability,
        )
        row = result.normalized_rows[0]
        self.assertEqual(row, validate_normalized_observation(row))
        self.assertEqual("line-1", row["ad_group"]["platform_id"])
        self.assertEqual(
            {
                "source_row_reference": "row-2",
                "insertion_order_id": "io-1",
                "line_item_id": "line-1",
            },
            result.mapping_report["source_semantics"][
                "row_identity_context"
            ][0],
        )

    def test_xandr_exact_schema_requires_insertion_order_identity(self):
        payload = json.loads(json.dumps(self.xandr_fixture))
        del payload["rows"][0]["insertion_order_id"]
        with self.assertRaisesRegex(AdapterError, "Insertion|insertion"):
            self.xandr.normalize(
                payload,
                registration=self.xandr_registration,
                capability=self.xandr_capability,
            )

    def test_generic_mapping_is_closed_self_hashed_and_not_verified(self):
        governance = {"minimum_group_size_rule": "10"}
        (
            _,
            source,
            profile,
            validation,
            admission,
            _,
            durable,
        ) = self.durable_generic_admission(
            self.generic_fixture,
            self.generic_registration,
            governance,
        )
        self.assertIsNone(self.generic.detect(source).adapter_id)
        detection = self.generic.detect(source, profile=profile)
        self.assertEqual(self.generic_capability.adapter_id, detection.adapter_id)
        self.assertEqual("pre_scan_clear", pre_scan_obvious_privacy(source).status)
        self.assertTrue(validation.accepted)
        result = self.generic.normalize(
            self.generic_fixture,
            registration=self.generic_registration,
            capability=self.generic_capability,
            source_inventory=source,
            admission_validation=admission,
            admitted_source=durable,
            governance=governance,
            profile=profile,
        )
        row = result.normalized_rows[0]
        self.assertEqual(row, validate_normalized_observation(row))
        self.assertEqual("2.5", row["outcome"]["source_numeric_text"])
        profile = result.mapping_report["mapping_profile"]
        self.assertEqual(
            self.generic_fixture["reporting_metadata"][
                "header_fingerprint"
            ],
            profile["header_fingerprint"],
        )
        self.assertFalse(profile["export_verified"])
        self.assertFalse(result.mapping_report["contract_ready"])

    def generic_source_inventory(
        self,
        source_container: str,
        *,
        headers: list[str] | None = None,
        payload: dict[str, object] | None = None,
    ) -> ContainerInventory:
        source_payload = (
            self.generic_fixture if payload is None else payload
        )
        source_headers = (
            source_payload["reporting_metadata"]["headers"]
            if headers is None
            else headers
        )
        values = source_payload["rows"][0]["values"]
        media_types = {
            "csv": "text/csv",
            "tsv": "text/tab-separated-values",
            "xlsx": (
                "application/vnd.openxmlformats-officedocument."
                "spreadsheetml.sheet"
            ),
        }
        return inventory(
            media_type=media_types[source_container],
            headers=tuple(source_headers),
            rows=(tuple(values[header] for header in source_headers),),
        )

    def generic_source_snapshot(
        self,
        payload: dict[str, object],
        source_container: str,
    ):
        self.generic_source_index += 1
        source_path = (
            self.generic_base
            / f"generic-{self.generic_source_index}.{source_container}"
        )
        headers = payload["reporting_metadata"]["headers"]
        rows = [
            [row["values"][header] for header in headers]
            for row in payload["rows"]
        ]
        if source_container in {"csv", "tsv"}:
            output = StringIO(newline="")
            writer = csv.writer(
                output,
                delimiter="," if source_container == "csv" else "\t",
                lineterminator="\n",
            )
            writer.writerow(headers)
            writer.writerows(rows)
            source_path.write_text(
                output.getvalue(), encoding="utf-8", newline=""
            )
        else:
            workbook = Workbook()
            sheet = workbook.active
            sheet.title = "data"
            sheet.append(headers)
            for row in rows:
                sheet.append(row)
            workbook_bytes = BytesIO()
            workbook.save(workbook_bytes)
            source_path.write_bytes(workbook_bytes.getvalue())
        snapshot = snapshot_source(
            source_path,
            staging_root=self.generic_base / "stage",
        )
        payload["source_sha256"] = snapshot.source_sha256
        return snapshot, inspect_container(snapshot)

    def durable_generic_admission(
        self,
        payload: dict[str, object],
        registration: dict[str, object],
        governance: dict[str, object],
    ):
        source_container = payload["reporting_metadata"]["source_container"]
        snapshot, source = self.generic_source_snapshot(
            payload, source_container
        )
        profile = self.generic.approved_profile(
            payload,
            registration=registration,
            capability=self.generic_capability,
        )
        adapter_inventory = self.generic.inventory(
            source,
            self.generic_capability,
            profile=profile,
        )
        validation = self.generic.validate(
            adapter_inventory,
            registration=registration,
            governance=governance,
            capability=self.generic_capability,
        )
        adapter_admission = self.generic.admission_validation(
            source,
            source_sha256=snapshot.source_sha256,
            validation=validation,
            registration=registration,
            governance=governance,
            profile=profile,
        )
        pre_scan = pre_scan_obvious_privacy(source)
        self.generic_source_index += 1
        durable = admit_source(
            snapshot,
            source,
            pre_scan,
            adapter_admission,
            self.generic_base
            / f"admitted-{self.generic_source_index}.{source_container}",
        )
        return (
            snapshot,
            source,
            profile,
            validation,
            adapter_admission,
            pre_scan,
            durable,
        )

    def test_generic_completes_explicit_admission_for_csv_tsv_and_xlsx(self):
        for source_container in ("csv", "tsv", "xlsx"):
            with self.subTest(source_container=source_container):
                payload = json.loads(json.dumps(self.generic_fixture))
                registration = json.loads(
                    json.dumps(self.generic_registration)
                )
                payload["reporting_metadata"][
                    "source_container"
                ] = source_container
                registration["approved_source_container"] = source_container
                governance = {"minimum_group_size_rule": "10"}
                (
                    _,
                    source,
                    profile,
                    validation,
                    admission,
                    _,
                    durable,
                ) = self.durable_generic_admission(
                    payload,
                    registration,
                    governance,
                )
                self.assertIsNone(self.generic.detect(source).adapter_id)
                self.assertEqual(
                    self.generic_capability.adapter_id,
                    self.generic.detect(source, profile=profile).adapter_id,
                )
                self.assertEqual(1000, validation.observed_minimum_group_size)
                result = self.generic.normalize(
                    payload,
                    registration=registration,
                    capability=self.generic_capability,
                    source_inventory=source,
                    admission_validation=admission,
                    admitted_source=durable,
                    governance=governance,
                    profile=profile,
                )
                self.assertEqual(1, len(result.normalized_rows))
                self.assertFalse(result.mapping_report["contract_ready"])

    def test_generic_admission_rejects_unapproved_shape_and_denominator(self):
        profile = self.generic.approved_profile(
            self.generic_fixture,
            registration=self.generic_registration,
            capability=self.generic_capability,
        )
        extra_headers = [
            *self.generic_fixture["reporting_metadata"]["headers"],
            "Unexpected",
        ]
        source = inventory(
            media_type="text/tab-separated-values",
            headers=tuple(extra_headers),
            rows=(
                tuple(
                    [
                        *self.generic_fixture["rows"][0]["values"].values(),
                        "value",
                    ]
                ),
            ),
        )
        self.assertIsNone(
            self.generic.detect(source, profile=profile).adapter_id
        )
        with self.assertRaisesRegex(AdapterError, "approved generic profile"):
            self.generic.inventory(
                source,
                self.generic_capability,
                profile=profile,
            )

        payload = json.loads(json.dumps(self.generic_fixture))
        registration = json.loads(json.dumps(self.generic_registration))
        delivered = "Delivered"
        payload["mapping"].pop(delivered)
        payload["reporting_metadata"]["headers"].remove(delivered)
        payload["rows"][0]["values"].pop(delivered)
        fingerprint = sha256_json(
            sorted(payload["reporting_metadata"]["headers"])
        )
        payload["reporting_metadata"]["header_fingerprint"] = fingerprint
        registration["approved_mapping"] = payload["mapping"]
        registration["approved_header_fingerprint"] = fingerprint
        with self.assertRaisesRegex(AdapterError, "impressions"):
            self.generic.approved_profile(
                payload,
                registration=registration,
                capability=self.generic_capability,
            )

        unsupported = json.loads(json.dumps(self.generic_fixture))
        unsupported_registration = json.loads(
            json.dumps(self.generic_registration)
        )
        unsupported["reporting_metadata"]["source_container"] = "json"
        unsupported_registration["approved_source_container"] = "json"
        with self.assertRaisesRegex(AdapterError, "CSV, TSV, or simple XLSX"):
            self.generic.approved_profile(
                unsupported,
                registration=unsupported_registration,
                capability=self.generic_capability,
            )

    def test_generic_admission_rejects_names_and_sensitive_identifiers(self):
        for replacement, message in (
            ("Creative Name", "name-only"),
            ("Device ID", "person, user, device, or event"),
        ):
            with self.subTest(replacement=replacement):
                payload = json.loads(json.dumps(self.generic_fixture))
                registration = json.loads(
                    json.dumps(self.generic_registration)
                )
                source = "Creative Key"
                target = payload["mapping"].pop(source)
                payload["mapping"][replacement] = target
                headers = payload["reporting_metadata"]["headers"]
                headers[headers.index(source)] = replacement
                value = payload["rows"][0]["values"].pop(source)
                payload["rows"][0]["values"][replacement] = value
                fingerprint = sha256_json(sorted(headers))
                payload["reporting_metadata"][
                    "header_fingerprint"
                ] = fingerprint
                registration["approved_mapping"] = payload["mapping"]
                registration["approved_header_fingerprint"] = fingerprint
                with self.assertRaisesRegex(AdapterError, message):
                    self.generic.approved_profile(
                        payload,
                        registration=registration,
                        capability=self.generic_capability,
                    )

    def test_generic_mapping_rejects_normalized_business_data_variants(self):
        prohibited_headers = (
            "Revenue",
            "TOTAL.REVENUE",
            "Gross-Revenue",
            "grossRevenue",
            "Sales",
            "net_sales",
            "Retention",
            "retention-rate",
            "CRM Outcome",
            "CRMOutcome",
            "web.analytics.result",
            "Customer Segment",
            "CustomerSegment",
            "customer_id",
            "Purchase Value",
            "purchase-value",
            "PurchaseValue",
            "Order Value",
            "order.value",
            "OrderValue",
            "Lifetime Value",
            "lifetime-value",
            "LifetimeValue",
            "LTV",
            "Customer Lifetime Value",
        )
        for prohibited_header in prohibited_headers:
            with self.subTest(source_header=prohibited_header):
                with self.assertRaisesRegex(AdapterError, "business data"):
                    validate_generic_mapping(
                        {
                            "Campaign Key": "campaign_id",
                            "Creative Key": "creative_id",
                            prohibited_header: "conversion_value",
                        },
                        sealed_delivery_map={
                            "sealed_before_outcome_access": True,
                        },
                        outcomes_accessed=True,
                    )

    def test_generic_mapping_rejects_business_data_targets(self):
        for target in (
            "revenue",
            "sales",
            "retention",
            "crm",
            "analytics",
            "customer",
            "purchase_value",
            "order_value",
            "lifetime_value",
            "ltv",
        ):
            with self.subTest(target=target):
                with self.assertRaisesRegex(AdapterError, "business data"):
                    validate_generic_mapping(
                        {
                            "Campaign Key": "campaign_id",
                            "Creative Key": "creative_id",
                            "Approved Conversion Count": target,
                        },
                        sealed_delivery_map={
                            "sealed_before_outcome_access": True,
                        },
                        outcomes_accessed=True,
                    )

    def test_generic_durable_chain_rejects_business_value_aliases(self):
        prohibited_headers = (
            "GMV",
            "G.M.V.",
            "Gross Merchandise Value",
            "Gross   Merchandise   Value",
            "gross-merchandise-values",
            "grossMerchandiseValue",
            "Gross Merchandise Volume",
            "Gross   Merchandise   Volume",
            "gross-merchandise-volumes",
            "gross.merchandise.volume",
            "grossMerchandiseVolume",
            "grossMerchandiseVolumes",
            "AOV",
            "A.O.V.",
            "Average Order Value",
            "average-order-values",
            "averageOrderValue",
            "CLV",
            "C.L.V.",
            "Customer Lifetime Value",
            "customer-lifetime-values",
            "customerLifetimeValue",
            "Lifetime Value",
            "lifetime-values",
            "lifetimeValue",
            "ROAS",
            "R.O.A.S.",
            "Return on Ad Spend",
            "return-on-ad-spend",
            "returnOnAdSpend",
            "Return on Advertising Spend",
            "Return   on   Advertising   Spend",
            "return-on-advertising-spends",
            "return.on.advertising.spend",
            "returnOnAdvertisingSpend",
            "returnOnAdvertisingSpends",
            "Returns on Ads Spends",
            "Purchase Amount",
            "purchase-amounts",
            "purchaseAmount",
            "Purchase Value",
            "purchase-values",
            "PurchaseValue",
            "Purchases Value",
            "Purchase Revenue",
            "purchase-revenues",
            "purchaseRevenue",
            "Purchase Total",
            "Purchase   Total",
            "purchase-totals",
            "purchase.total",
            "purchaseTotal",
            "purchaseTotals",
            "Purchases Totals",
            "Purchase Total Count",
            "purchase-total-counts",
            "purchaseTotalCount",
            "Count Purchase Total",
            "counts-purchase-total",
            "countPurchaseTotal",
            "Order Amount",
            "order-amounts",
            "orderAmount",
            "Order Value",
            "order-values",
            "OrderValue",
            "Order Revenue",
            "order-revenues",
            "orderRevenue",
            "Orders Revenue",
            "Order Total",
            "Order   Total",
            "order-totals",
            "order.total",
            "orderTotal",
            "orderTotals",
            "Orders Totals",
            "Order Total Rate",
            "order-total-rates",
            "orderTotalRate",
            "Rate Order Total",
            "rates-order-total",
            "rateOrderTotal",
        )
        for prohibited_header in prohibited_headers:
            with self.subTest(source_header=prohibited_header):
                payload = json.loads(json.dumps(self.generic_fixture))
                registration = json.loads(
                    json.dumps(self.generic_registration)
                )
                original = "Approved Purchase"
                target = payload["mapping"].pop(original)
                payload["mapping"][prohibited_header] = target
                headers = payload["reporting_metadata"]["headers"]
                headers[headers.index(original)] = prohibited_header
                values = payload["rows"][0]["values"]
                values[prohibited_header] = values.pop(original)
                payload["reporting_metadata"][
                    "conversion_metric"
                ] = prohibited_header
                fingerprint = sha256_json(sorted(headers))
                payload["reporting_metadata"][
                    "header_fingerprint"
                ] = fingerprint
                registration["registered_source_metric"] = prohibited_header
                registration["approved_mapping"] = payload["mapping"]
                registration[
                    "approved_header_fingerprint"
                ] = fingerprint
                governance = {"minimum_group_size_rule": "10"}
                with self.assertRaisesRegex(AdapterError, "business data"):
                    (
                        _,
                        source,
                        profile,
                        _,
                        admission,
                        _,
                        durable,
                    ) = self.durable_generic_admission(
                        payload,
                        registration,
                        governance,
                    )
                    self.generic.normalize(
                        payload,
                        registration=registration,
                        capability=self.generic_capability,
                        source_inventory=source,
                        admission_validation=admission,
                        admitted_source=durable,
                        governance=governance,
                        profile=profile,
                    )

    def test_generic_durable_chain_keeps_counts_and_rates_available(self):
        for allowed_header in (
            "Purchase Count",
            "Purchases",
            "Purchase Rate",
            "Purchase Rate Total",
            "Purchase Count Total",
            "purchase-count-total",
            "purchaseCountTotal",
            "Total Purchases",
            "totalPurchases",
            "Conversion Count",
            "Conversions",
            "Conversion Rate",
            "Order Rate Total",
            "Order Count Total",
            "order.rate.total",
            "orderRateTotal",
            "Total Orders",
            "totalOrders",
        ):
            with self.subTest(source_header=allowed_header):
                payload = json.loads(json.dumps(self.generic_fixture))
                registration = json.loads(
                    json.dumps(self.generic_registration)
                )
                original = "Approved Purchase"
                target = payload["mapping"].pop(original)
                payload["mapping"][allowed_header] = target
                headers = payload["reporting_metadata"]["headers"]
                headers[headers.index(original)] = allowed_header
                values = payload["rows"][0]["values"]
                values[allowed_header] = values.pop(original)
                payload["reporting_metadata"][
                    "conversion_metric"
                ] = allowed_header
                fingerprint = sha256_json(sorted(headers))
                payload["reporting_metadata"][
                    "header_fingerprint"
                ] = fingerprint
                registration["registered_source_metric"] = allowed_header
                registration["approved_mapping"] = payload["mapping"]
                registration[
                    "approved_header_fingerprint"
                ] = fingerprint
                governance = {"minimum_group_size_rule": "10"}
                (
                    _,
                    source,
                    profile,
                    _,
                    admission,
                    _,
                    durable,
                ) = self.durable_generic_admission(
                    payload,
                    registration,
                    governance,
                )
                result = self.generic.normalize(
                    payload,
                    registration=registration,
                    capability=self.generic_capability,
                    source_inventory=source,
                    admission_validation=admission,
                    admitted_source=durable,
                    governance=governance,
                    profile=profile,
                )
                self.assertEqual(1, len(result.normalized_rows))

    def test_generic_mapping_keeps_aggregate_ad_metrics_available(self):
        mapping = validate_generic_mapping(
            {
                "Campaign Key": "campaign_id",
                "Creative Key": "creative_id",
                "Delivered Impressions": "impressions",
                "Click Count": "clicks",
                "Media Spend": "spend",
                "Approved Conversion Count": "conversion_value",
            },
            sealed_delivery_map={"sealed_before_outcome_access": True},
            outcomes_accessed=True,
        )
        self.assertEqual(
            "conversion_value", mapping["Approved Conversion Count"]
        )

    def test_generic_normalize_cannot_bypass_explicit_admission(self):
        with self.assertRaisesRegex(AdapterError, "durable admitted source"):
            self.generic.normalize(
                self.generic_fixture,
                registration=self.generic_registration,
                capability=self.generic_capability,
            )

    def test_generic_normalize_is_bound_to_admitted_source_values(self):
        governance = {"minimum_group_size_rule": "10"}
        (
            _,
            source,
            profile,
            _,
            admission,
            _,
            durable,
        ) = self.durable_generic_admission(
            self.generic_fixture,
            self.generic_registration,
            governance,
        )
        changed = json.loads(json.dumps(self.generic_fixture))
        changed["rows"][0]["values"]["Media Spend"] = "999.00"
        with self.assertRaisesRegex(AdapterError, "admitted source"):
            self.generic.normalize(
                changed,
                registration=self.generic_registration,
                capability=self.generic_capability,
                source_inventory=source,
                admission_validation=admission,
                admitted_source=durable,
                governance=governance,
                profile=profile,
            )

    def test_generic_profile_and_governance_replay_cannot_reuse_admission(self):
        payload_a = json.loads(json.dumps(self.generic_fixture))
        registration_a = json.loads(json.dumps(self.generic_registration))
        governance_a = {"minimum_group_size_rule": "100"}
        (
            _,
            source,
            profile_a,
            _,
            adapter_admission_a,
            _,
            durable_a,
        ) = self.durable_generic_admission(
            payload_a,
            registration_a,
            governance_a,
        )

        payload_b = json.loads(json.dumps(payload_a))
        registration_b = json.loads(json.dumps(registration_a))
        payload_b["mapping"]["Delivered"] = "clicks"
        payload_b["mapping"]["Click Count"] = "impressions"
        payload_b["reporting_metadata"][
            "mapping_profile_id"
        ] = "mapping-profile-2"
        registration_b["approved_mapping"] = payload_b["mapping"]
        registration_b[
            "approved_mapping_profile_id"
        ] = "mapping-profile-2"
        profile_b = self.generic.approved_profile(
            payload_b,
            registration=registration_b,
            capability=self.generic_capability,
        )
        inventory_b = self.generic.inventory(
            source,
            self.generic_capability,
            profile=profile_b,
        )
        failed_b = self.generic.validate(
            inventory_b,
            registration=registration_b,
            governance=governance_a,
            capability=self.generic_capability,
        )
        self.assertEqual(12, failed_b.observed_minimum_group_size)
        self.assertFalse(failed_b.accepted)
        with self.assertRaisesRegex(AdapterError, "admission|validation"):
            self.generic.normalize(
                payload_b,
                registration=registration_b,
                capability=self.generic_capability,
                source_inventory=source,
                admission_validation=adapter_admission_a,
                admitted_source=durable_a,
                governance=governance_a,
                profile=profile_b,
            )

        governance_b = {"minimum_group_size_rule": "10"}
        passed_b = self.generic.validate(
            inventory_b,
            registration=registration_b,
            governance=governance_b,
            capability=self.generic_capability,
        )
        self.assertTrue(passed_b.accepted)
        with self.assertRaisesRegex(AdapterError, "profile|admission"):
            self.generic.normalize(
                payload_b,
                registration=registration_b,
                capability=self.generic_capability,
                source_inventory=source,
                admission_validation=adapter_admission_a,
                admitted_source=durable_a,
                governance=governance_b,
                profile=profile_b,
            )

        changed_governance = {"minimum_group_size_rule": "200"}
        with self.assertRaisesRegex(AdapterError, "governance|admission"):
            self.generic.normalize(
                payload_a,
                registration=registration_a,
                capability=self.generic_capability,
                source_inventory=source,
                admission_validation=adapter_admission_a,
                admitted_source=durable_a,
                governance=changed_governance,
                profile=profile_a,
            )

    def test_generic_privacy_pre_scan_cannot_be_bypassed(self):
        payload = json.loads(json.dumps(self.generic_fixture))
        registration = json.loads(json.dumps(self.generic_registration))
        original = "Media Spend"
        blocked = "access_token"
        target = payload["mapping"].pop(original)
        payload["mapping"][blocked] = target
        headers = payload["reporting_metadata"]["headers"]
        headers[headers.index(original)] = blocked
        value = payload["rows"][0]["values"].pop(original)
        payload["rows"][0]["values"][blocked] = value
        fingerprint = sha256_json(sorted(headers))
        payload["reporting_metadata"]["header_fingerprint"] = fingerprint
        registration["approved_mapping"] = payload["mapping"]
        registration["approved_header_fingerprint"] = fingerprint
        snapshot, source = self.generic_source_snapshot(payload, "tsv")
        profile = self.generic.approved_profile(
            payload,
            registration=registration,
            capability=self.generic_capability,
        )
        adapter_inventory = self.generic.inventory(
            source,
            self.generic_capability,
            profile=profile,
        )
        governance = {"minimum_group_size_rule": "100"}
        validation = self.generic.validate(
            adapter_inventory,
            registration=registration,
            governance=governance,
            capability=self.generic_capability,
        )
        self.assertTrue(validation.accepted)
        adapter_admission = self.generic.admission_validation(
            source,
            source_sha256=snapshot.source_sha256,
            validation=validation,
            registration=registration,
            governance=governance,
            profile=profile,
        )
        blocked_scan = pre_scan_obvious_privacy(source)
        self.assertIn("secret_header", blocked_scan.blocked_categories)
        with self.assertRaisesRegex(PrivacyAdmissionError, "privacy"):
            admit_source(
                snapshot,
                source,
                blocked_scan,
                adapter_admission,
                self.generic_base / "blocked.tsv",
            )
        fabricated = AdmittedSource(
            source_path=snapshot.staged_path,
            source_sha256=snapshot.source_sha256,
            byte_length=snapshot.byte_length,
            source_name="fabricated.tsv",
            snapshot_sha256="sha256:" + ("0" * 64),
            inventory_sha256=container_inventory_sha256(source),
            pre_scan_sha256="sha256:" + ("0" * 64),
            adapter_validation_sha256="sha256:" + ("0" * 64),
            admission_sha256="sha256:" + ("0" * 64),
        )
        with self.assertRaisesRegex(AdapterError, "privacy|admission"):
            self.generic.normalize(
                payload,
                registration=registration,
                capability=self.generic_capability,
                source_inventory=source,
                admission_validation=adapter_admission,
                admitted_source=fabricated,
                governance=governance,
                profile=profile,
            )

    def test_generic_manual_adapter_receipt_cannot_authorize_normalization(self):
        payload = json.loads(json.dumps(self.generic_fixture))
        registration = json.loads(json.dumps(self.generic_registration))
        governance = {"minimum_group_size_rule": "100"}
        (
            _,
            source,
            profile,
            _,
            adapter_admission,
            _,
            _,
        ) = self.durable_generic_admission(
            payload,
            registration,
            governance,
        )
        fabricated = AdapterAdmissionValidation(
            adapter_id=adapter_admission.adapter_id,
            adapter_version=adapter_admission.adapter_version,
            source_sha256=adapter_admission.source_sha256,
            inventory_sha256=adapter_admission.inventory_sha256,
            profile_sha256=adapter_admission.profile_sha256,
            adapter_validation_sha256=(
                adapter_admission.adapter_validation_sha256
            ),
            governance_sha256=adapter_admission.governance_sha256,
            accepted=True,
            observed_minimum_group_size=(
                adapter_admission.observed_minimum_group_size
            ),
            errors=(),
        )
        with self.assertRaisesRegex(AdapterError, "durable|admission"):
            self.generic.normalize(
                payload,
                registration=registration,
                capability=self.generic_capability,
                source_inventory=source,
                admission_validation=fabricated,
                governance=governance,
                profile=profile,
            )

    def test_generic_structurally_valid_manual_durable_receipt_is_rejected(self):
        payload = json.loads(json.dumps(self.generic_fixture))
        registration = json.loads(json.dumps(self.generic_registration))
        governance = {"minimum_group_size_rule": "100"}
        snapshot, source = self.generic_source_snapshot(payload, "tsv")
        profile = self.generic.approved_profile(
            payload,
            registration=registration,
            capability=self.generic_capability,
        )
        adapter_inventory = self.generic.inventory(
            source,
            self.generic_capability,
            profile=profile,
        )
        validation = self.generic.validate(
            adapter_inventory,
            registration=registration,
            governance=governance,
            capability=self.generic_capability,
        )
        adapter_admission = self.generic.admission_validation(
            source,
            source_sha256=snapshot.source_sha256,
            validation=validation,
            registration=registration,
            governance=governance,
            profile=profile,
        )
        pre_scan = pre_scan_obvious_privacy(source)
        manual = AdmittedSource(
            source_path=snapshot.staged_path.resolve(),
            source_sha256=snapshot.source_sha256,
            byte_length=snapshot.byte_length,
            source_name=snapshot.original_path.name,
            snapshot_sha256=source_snapshot_sha256(snapshot),
            inventory_sha256=container_inventory_sha256(source),
            pre_scan_sha256=privacy_decision_sha256(pre_scan),
            adapter_validation_sha256=(
                adapter_admission_validation_sha256(
                    adapter_admission
                )
            ),
            admission_sha256="sha256:" + ("0" * 64),
        )
        manual = replace(
            manual,
            admission_sha256=sha256_json(
                {
                    "source_path": str(manual.source_path),
                    "source_sha256": manual.source_sha256,
                    "byte_length": manual.byte_length,
                    "source_name": manual.source_name,
                    "snapshot_sha256": manual.snapshot_sha256,
                    "inventory_sha256": manual.inventory_sha256,
                    "pre_scan_sha256": manual.pre_scan_sha256,
                    "adapter_validation_sha256": (
                        manual.adapter_validation_sha256
                    ),
                }
            ),
        )
        with self.assertRaisesRegex(AdapterError, "admission"):
            self.generic.normalize(
                payload,
                registration=registration,
                capability=self.generic_capability,
                source_inventory=source,
                admission_validation=adapter_admission,
                admitted_source=manual,
                governance=governance,
                profile=profile,
            )

    def test_generic_durable_admission_rejects_tampered_chain_hashes(self):
        payload = json.loads(json.dumps(self.generic_fixture))
        registration = json.loads(json.dumps(self.generic_registration))
        governance = {"minimum_group_size_rule": "100"}
        (
            _,
            source,
            profile,
            _,
            adapter_admission,
            _,
            durable,
        ) = self.durable_generic_admission(
            payload,
            registration,
            governance,
        )
        for field in (
            "source_sha256",
            "snapshot_sha256",
            "inventory_sha256",
            "pre_scan_sha256",
            "adapter_validation_sha256",
            "admission_sha256",
        ):
            with self.subTest(field=field):
                forged = replace(
                    durable,
                    **{field: "sha256:" + ("0" * 64)},
                )
                with self.assertRaisesRegex(
                    AdapterError, "admission|validation"
                ):
                    self.generic.normalize(
                        payload,
                        registration=registration,
                        capability=self.generic_capability,
                        source_inventory=source,
                        admission_validation=adapter_admission,
                        admitted_source=forged,
                        governance=governance,
                        profile=profile,
                    )

    def test_generic_platform_must_match_closed_source_and_delivery_identity(self):
        for source_platform, delivery_platform in (
            ("generic-dsp", "generic_dsp"),
            ("generic_dsp", "unrelated_dsp"),
        ):
            with self.subTest(
                source_platform=source_platform,
                delivery_platform=delivery_platform,
            ):
                payload = json.loads(json.dumps(self.generic_fixture))
                registration = json.loads(
                    json.dumps(self.generic_registration)
                )
                payload["reporting_metadata"][
                    "source_platform"
                ] = source_platform
                registration["sealed_delivery_map"]["mappings"][0][
                    "platform"
                ] = delivery_platform
                with self.assertRaisesRegex(AdapterError, "platform"):
                    self.generic.approved_profile(
                        payload,
                        registration=registration,
                        capability=self.generic_capability,
                    )

    def test_generic_rejects_unregistered_passthrough_and_name_only_ids(self):
        with self.assertRaisesRegex(AdapterError, "unsupported targets"):
            validate_generic_mapping(
                {"Creative Name": "creative_name"},
                sealed_delivery_map={
                    "sealed_before_outcome_access": True,
                },
                outcomes_accessed=False,
            )
        payload = json.loads(json.dumps(self.generic_fixture))
        payload["reporting_metadata"]["stable_id_targets"] = [
            "campaign_id",
            "line_item_id",
        ]
        with self.assertRaisesRegex(AdapterError, "stable IDs"):
            self.generic.approved_profile(
                payload,
                registration=self.generic_registration,
                capability=self.generic_capability,
            )

        tampered = json.loads(json.dumps(self.generic_fixture))
        tampered["mapping"]["Line Key"] = "ad_group_id"
        with self.assertRaisesRegex(AdapterError, "approved mapping"):
            self.generic.approved_profile(
                tampered,
                registration=self.generic_registration,
                capability=self.generic_capability,
            )

        for field, value, message in (
            ("Campaign Key", "N/A", "null token"),
            ("Approved Purchase", "NULL", "unknown null token"),
        ):
            with self.subTest(field=field):
                payload = json.loads(json.dumps(self.generic_fixture))
                payload["rows"][0]["values"][field] = value
                governance = {"minimum_group_size_rule": "10"}
                (
                    _,
                    source,
                    profile,
                    _,
                    admission,
                    _,
                    durable,
                ) = self.durable_generic_admission(
                    payload,
                    self.generic_registration,
                    governance,
                )
                with self.assertRaisesRegex(AdapterError, message):
                    self.generic.normalize(
                        payload,
                        registration=self.generic_registration,
                        capability=self.generic_capability,
                        source_inventory=source,
                        admission_validation=admission,
                        admitted_source=durable,
                        governance=governance,
                        profile=profile,
                    )

    def test_programmatic_adapters_reject_forged_maturity_or_variant_shape(self):
        for adapter_type, capability, mutation in (
            (
                DV360Adapter,
                self.dv360_capability,
                {"maturity": "export_verified"},
            ),
            (
                TradeDeskAdapter,
                self.ttd_capability,
                {"container": "tsv"},
            ),
            (
                AmazonDSPAdapter,
                self.capability(
                    "amazon-unified-reporting-api-json-v1"
                ),
                {
                    "maturity": "export_verified",
                    "availability_reason": None,
                },
            ),
            (
                XandrAdapter,
                self.xandr_capability,
                {"report_type": "advertiser_analytics_excel"},
            ),
            (
                GenericProgrammaticAdapter,
                self.generic_capability,
                {"contract_ready_permitted": True},
            ),
        ):
            with self.subTest(adapter=adapter_type.__name__):
                with self.assertRaisesRegex(
                    AdapterError,
                    "requires",
                ):
                    adapter_type(replace(capability, **mutation))

    def test_programmatic_adapters_authenticate_complete_capability_provenance(self):
        adapters = (
            (DV360Adapter, self.dv360_capability),
            (TradeDeskAdapter, self.ttd_capability),
            (
                AmazonDSPAdapter,
                self.capability("amazon-unified-reporting-api-json-v1"),
            ),
            (XandrAdapter, self.xandr_capability),
            (GenericProgrammaticAdapter, self.generic_capability),
        )
        for adapter_type, capability in adapters:
            for mutation in (
                {"adapter_version": "9.9.9"},
                {"schema_fingerprint": "sha256:" + "0" * 64},
                {"value_states": ("observed",)},
            ):
                with self.subTest(
                    adapter=adapter_type.__name__,
                    mutation=mutation,
                ):
                    with self.assertRaisesRegex(AdapterError, "requires"):
                        adapter_type(replace(capability, **mutation))


if __name__ == "__main__":
    unittest.main()
