from pathlib import Path
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "skills" / "real-world-outcome-data-prep" / "scripts"
sys.path.insert(0, str(SCRIPTS))

from outcome_data_prep.common import (  # noqa: E402
    ContractError,
    require_numeric_string,
    require_numeric_string_or_number,
)
from outcome_data_prep import contracts as prep_contracts  # noqa: E402
from outcome_data_prep.contracts import (  # noqa: E402
    validate_delivery_map,
    validate_correction_request,
    validate_normalized_observation,
    validate_registration_receipt,
    validate_readiness_report,
    validate_source_governance_input,
    validate_source_governance_record,
)


class OutcomePrepContractTests(unittest.TestCase):
    def normalized_observation(self):
        return {
            "schema_version": "normalized-outcome-observation-v1",
            "observation_id": "observation-one",
            "study_id": "study-one",
            "registration_id": "registration-one",
            "import_id": "import-one",
            "source_id": "source-one",
            "source_sha256": "sha256:" + "a" * 64,
            "source_row_reference": "results[0]",
            "platform": "google_ads",
            "adapter": {
                "adapter_id": "google-ads-api-v23-ad-daily-json",
                "adapter_version": "1.0.0",
                "maturity": "schema_tested",
            },
            "account": {"platform_id": "12345678901234567890"},
            "campaign": {"platform_id": "23456789012345678901"},
            "ad_group": {"platform_id": "34567890123456789012"},
            "ad": {"platform_id": "45678901234567890123"},
            "creative": {"platform_id": "45678901234567890123"},
            "reporting": {
                "start_date": "2026-07-01",
                "end_date": "2026-07-01",
                "timezone": "America/New_York",
                "basis": "interaction_date",
                "request_level": None,
                "time_increment": None,
                "segment_grain": [
                    "customer.id",
                    "campaign.id",
                    "ad_group.id",
                    "ad_group_ad.ad.id",
                    "segments.date",
                ],
                "latency_state": "mature",
                "observed_at": "2026-07-10T12:00:00-04:00",
            },
            "attribution": {
                "report_time": "interaction_date",
                "windows": [],
            },
            "currency": {
                "code": "USD",
                "basis": "customer_currency",
            },
            "spend": {
                "value": 123.456789,
                "decimal": "123.456789",
                "source_numeric_text": "123456789",
                "source_metric": "cost_micros",
                "source_unit": "micros",
            },
            "exposure": {
                "impressions": {
                    "value": 1000,
                    "source_numeric_text": "1000",
                },
                "clicks": {
                    "value": 12,
                    "source_numeric_text": "12",
                },
            },
            "outcome": {
                "metric_id": "purchase",
                "source_metric": "conversions",
                "value": 1.5,
                "decimal": "1.5",
                "source_numeric_text": "1.5",
                "value_state": "fractional",
                "omitted_zero_behavior": (
                    "rows_omitted_when_all_metrics_zero"
                ),
            },
            "platform_semantics": {
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
            "validation_projection": {
                "status": "unavailable",
                "evidence_status": "blocked",
                "metric_family": None,
                "measurement_window": None,
                "attribution_window": None,
                "aggregate": None,
                "eligible_exposure_count": None,
                "missing_outcome_count": None,
                "effective_sample_size": None,
                "assignment": None,
                "confidence_level": None,
                "permission_confirmed": None,
                "outcome_accessed_at": None,
                "limitations": [],
            },
            "normalized_observation_sha256": None,
        }

    def delivery_map(self):
        return {
            "schema_version": "outcome-delivery-map-v1",
            "study_id": "study-one",
            "registration_id": "study-one",
            "sealed_before_outcome_access": True,
            "mappings": [{
                "mapping_id": "mapping-one",
                "platform": "meta",
                "platform_campaign_id": "campaign-one",
                "platform_ad_group_id": "ad-group-one",
                "platform_ad_id": "ad-one",
                "platform_creative_id": "creative-one",
                "block_id": "block-one",
                "study_id": "study-one",
                "arm_id": "arm-one",
                "batch_id": "batch-one",
                "segment_ids": ["segment-one"],
                "creative_id": "creative-one",
                "variant_id": "creative-one",
                "asset_sha256": "sha256:" + "a" * 64,
                "campaign_plan_sha256": "sha256:" + "b" * 64,
            }],
            "chronology": {
                "events": [{
                    "event_type": "delivery_map_sealed",
                    "occurred_at": "2026-07-31T10:00:00Z",
                    "evidence_source_sha256": "sha256:" + "c" * 64,
                    "attested_by": "operator-one",
                    "attested_at": "2026-07-31T10:00:00Z",
                    "authority_id": "operator-one",
                }]
            },
            "delivery_map_sha256": None,
        }

    def governance_input(self):
        return {
            "schema_version": "outcome-source-governance-input-v1",
            "data_owner": "Acme",
            "system_of_record": "Acme reporting export",
            "permission_reference": "permission-one",
            "confirmer": "operator-one",
            "allowed_purpose": "outcome validation preparation",
            "retention_policy": "30 days",
            "minimum_group_size_rule": "10",
            "restricted_fields_removed_attestation": True,
            "export_method": "uploaded file",
            "export_timestamp": "2026-07-31T12:00:00Z",
            "source_governance_input_sha256": None,
        }

    def trusted_runtime(self):
        return {
            "observed_minimum_group_size": 10,
            "protected_staging_location": "/trusted/staging/source-one.csv",
            "source_filename": "source-one.csv",
            "source_sha256": "sha256:" + "a" * 64,
            "aggregate_only": True,
            "person_level_data": False,
            "adapter_name": "meta-ads-csv",
            "adapter_version": "1.0.0",
        }

    def test_delivery_map_rejects_unknown_keys(self):
        with self.assertRaisesRegex(ContractError, "unknown fields"):
            validate_delivery_map({
                "schema_version": "outcome-delivery-map-v1",
                "study_id": "study-one",
                "registration_id": "study-one",
                "sealed_before_outcome_access": True,
                "mappings": [],
                "chronology": {"events": []},
                "delivery_map_sha256": None,
                "helpful_extension": {},
            })

    def test_delivery_map_accepts_exact_supplied_self_hash(self):
        sealed = validate_delivery_map(self.delivery_map())
        self.assertEqual(sealed, validate_delivery_map(sealed))

    def test_delivery_map_rejects_invalid_supplied_digest(self):
        payload = self.delivery_map()
        payload["delivery_map_sha256"] = "sha256:" + "g" * 64
        with self.assertRaisesRegex(ContractError, "SHA-256"):
            validate_delivery_map(payload)

    def test_task1_registration_receipt_v1_shape_remains_compatible(self):
        receipt = {
            "schema_version": "outcome-registration-receipt-v1",
            "registration_id": "registration-one",
            "study_id": "study-one",
            "registered_at": "2026-07-31T10:00:00Z",
            "registered_by": "operator-one",
            "study_setup_sha256": "sha256:" + "a" * 64,
            "delivery_map_sha256": "sha256:" + "b" * 64,
            "creative_manifest_sha256": "sha256:" + "c" * 64,
            "registration_receipt_sha256": None,
        }
        sealed = validate_registration_receipt(receipt)
        self.assertEqual(sealed, validate_registration_receipt(sealed))
        with self.assertRaisesRegex(ContractError, "unknown fields"):
            validate_registration_receipt({**sealed, "receipt_hmac_sha256": "x"})

    def test_authenticated_registration_receipt_has_a_new_closed_version(self):
        self.assertTrue(
            hasattr(prep_contracts, "validate_authenticated_registration_receipt")
        )
        receipt = {
            "schema_version": "outcome-registration-receipt-v2",
            "study_id": "study-one",
            "registration_id": "registration-one",
            "registration_sha256": "sha256:" + "a" * 64,
            "delivery_map_sha256": "sha256:" + "b" * 64,
            "creative_manifest_sha256": "sha256:" + "c" * 64,
            "chronology": {
                "events": [
                    {
                        "event_type": "registration_sealed",
                        "occurred_at": "2026-07-31T10:00:00Z",
                        "evidence_source_sha256": "sha256:" + "a" * 64,
                        "attested_by": "operator-one",
                        "attested_at": "2026-07-31T10:00:00Z",
                        "authority_id": "operator-one",
                    }
                ]
            },
            "evidence_status": "descriptive_only",
            "receipt_sha256": None,
            "receipt_hmac_sha256": "sha256:" + "d" * 64,
        }
        validator = prep_contracts.validate_authenticated_registration_receipt
        sealed = validator(receipt)
        self.assertEqual(sealed, validator(sealed))
        with self.assertRaisesRegex(ContractError, "unknown fields"):
            validator({**sealed, "helpful_note": "trusted"})

    def test_schema_tested_adapter_cannot_be_contract_ready(self):
        with self.assertRaisesRegex(ContractError, "export_verified"):
            validate_readiness_report({
                "schema_version": "outcome-prep-readiness-v1",
                "study_id": "study-one",
                "import_id": "import-one",
                "evidence_status": "preregistered_holdout",
                "operational_status": "contract_ready",
                "adapter_maturity": "schema_tested",
                "reasons": [],
                "readiness_sha256": None,
            })

    def test_nonfinite_numeric_strings_are_rejected(self):
        for value in ("NaN", "Infinity", "-Infinity"):
            with self.subTest(value=value):
                with self.assertRaisesRegex(ContractError, "finite"):
                    require_numeric_string(value, "metric")

    def test_numeric_helpers_reject_bool_and_malformed_values(self):
        with self.assertRaisesRegex(ContractError, "numeric"):
            require_numeric_string_or_number(True, "metric")
        with self.assertRaisesRegex(ContractError, "numeric"):
            require_numeric_string("twelve", "metric")

    def test_numeric_helpers_preserve_valid_fractional_text(self):
        self.assertEqual("1.250", require_numeric_string("1.250", "metric"))

    def test_rich_normalized_observation_is_closed_self_hashed_and_defensive(self):
        source = self.normalized_observation()
        sealed = validate_normalized_observation(source)
        self.assertRegex(
            sealed["normalized_observation_sha256"],
            r"^sha256:[0-9a-f]{64}$",
        )
        self.assertEqual(sealed, validate_normalized_observation(sealed))
        source["outcome"]["source_metric"] = "all_conversions"
        self.assertEqual("conversions", sealed["outcome"]["source_metric"])

    def test_rich_normalized_observation_rejects_flat_or_extended_shapes(self):
        flat = {
            "schema_version": "normalized-outcome-observation-v1",
            "observation_id": "observation-one",
            "study_id": "study-one",
            "import_id": "import-one",
            "source_id": "source-one",
            "platform": "google_ads",
            "reporting_start_at": "2026-07-01T00:00:00Z",
            "reporting_end_at": "2026-07-01T00:00:00Z",
            "metric_id": "purchase",
            "metric_value": "1.5",
            "denominator_value": "1000",
            "normalized_observation_sha256": None,
        }
        with self.assertRaisesRegex(ContractError, "unknown fields"):
            validate_normalized_observation(flat)
        extended = self.normalized_observation()
        extended["reporting"]["helpful_note"] = "inferred"
        with self.assertRaisesRegex(ContractError, "unknown fields"):
            validate_normalized_observation(extended)

    def test_rich_normalized_observation_closes_platform_semantics(self):
        row = self.normalized_observation()
        row["platform_semantics"]["helpful_platform_note"] = "inferred"
        with self.assertRaisesRegex(ContractError, "unknown fields"):
            validate_normalized_observation(row)

        attacks = (
            (
                {
                    "billed_currency": "USD",
                    "currency_relationship": "not_applicable",
                },
                "currency_relationship",
            ),
            (
                {
                    "delivery_state": "standard",
                    "skan_state": "skan_delayed",
                },
                "skan_state",
            ),
            (
                {
                    "search_term_id": "-1",
                    "search_term_state": "observed",
                },
                "search_term",
            ),
        )
        for updates, message in attacks:
            with self.subTest(updates=updates):
                row = self.normalized_observation()
                row["platform_semantics"].update(updates)
                with self.assertRaisesRegex(ContractError, message):
                    validate_normalized_observation(row)

    def test_platform_semantics_are_bound_into_observation_self_hash(self):
        sealed = validate_normalized_observation(
            self.normalized_observation()
        )
        sealed["platform_semantics"]["click_semantic"] = "all_clicks"
        with self.assertRaisesRegex(ContractError, "canonical content"):
            validate_normalized_observation(sealed)

    def test_rich_normalized_observation_rejects_numeric_semantic_drift(self):
        for mutation, message in (
            (
                lambda row: row["spend"].__setitem__("value", True),
                "numeric",
            ),
            (
                lambda row: row["spend"].__setitem__(
                    "source_numeric_text", "123456788"
                ),
                "micros",
            ),
            (
                lambda row: row["outcome"].__setitem__(
                    "source_numeric_text", "1.6"
                ),
                "source_numeric_text",
            ),
            (
                lambda row: row["outcome"].__setitem__(
                    "value", float("nan")
                ),
                "finite",
            ),
        ):
            with self.subTest(message=message):
                row = self.normalized_observation()
                mutation(row)
                with self.assertRaisesRegex(ContractError, message):
                    validate_normalized_observation(row)

    def test_rich_normalized_observation_keeps_missing_states_distinct(self):
        for state in ("null", "absent", "suppressed", "omitted_zero"):
            with self.subTest(state=state):
                row = self.normalized_observation()
                row["outcome"].update(
                    {
                        "value": None,
                        "decimal": None,
                        "source_numeric_text": None,
                        "value_state": state,
                    }
                )
                sealed = validate_normalized_observation(row)
                self.assertEqual(state, sealed["outcome"]["value_state"])
                self.assertIsNone(sealed["outcome"]["value"])

    def test_rich_normalized_observation_enforces_canonical_numeric_state(self):
        attacks = (
            ("0", 0, "observed"),
            ("1.5", 1.5, "observed"),
            ("2", 2, "fractional"),
            ("1.5", 1.5, "observed_zero"),
            ("1", 1, "immature"),
        )
        for text, value, state in attacks:
            with self.subTest(value=value, state=state):
                row = self.normalized_observation()
                row["outcome"].update(
                    {
                        "value": value,
                        "decimal": text,
                        "source_numeric_text": text,
                        "value_state": state,
                    }
                )
                with self.assertRaisesRegex(
                    ContractError, "value_state"
                ):
                    validate_normalized_observation(row)

    def test_rich_normalized_observation_keeps_quality_and_latency_orthogonal(self):
        for state in ("modeled", "estimated"):
            with self.subTest(state=state):
                row = self.normalized_observation()
                row["reporting"]["latency_state"] = "immature"
                row["outcome"]["value_state"] = state
                sealed = validate_normalized_observation(row)
                self.assertEqual(
                    "immature", sealed["reporting"]["latency_state"]
                )
                self.assertEqual(state, sealed["outcome"]["value_state"])

    def test_governance_record_uses_keyword_only_trusted_runtime_facts(self):
        governance_input = validate_source_governance_input(self.governance_input())
        record = validate_source_governance_record(
            {
                "schema_version": "outcome-source-governance-record-v1",
                "governance_input": governance_input,
                "source_governance_record_sha256": None,
            },
            trusted_runtime=self.trusted_runtime(),
        )
        self.assertEqual("source-one.csv", record["source_filename"])
        self.assertEqual("sha256:" + "a" * 64, record["source_sha256"])

        with self.assertRaisesRegex(ContractError, "unknown fields"):
            validate_source_governance_record(
                {
                    "schema_version": "outcome-source-governance-record-v1",
                    "governance_input": governance_input,
                    "source_filename": "caller-asserted.csv",
                    "source_governance_record_sha256": None,
                },
                trusted_runtime=self.trusted_runtime(),
            )

    def correction_request(self, replacement_source_sha256):
        return {
            "schema_version": "outcome-correction-request-v1",
            "correction_id": "correction-one",
            "study_id": "study-one",
            "requested_at": "2026-07-31T12:00:00Z",
            "actor": "operator-one",
            "reason_code": "corrected-source",
            "reason": "The platform export was corrected.",
            "supersedes_import_id": "import-one",
            "supersedes_observation_ids": ["observation-one"],
            "expected_analytical_identity_sha256": "sha256:" + "c" * 64,
            "replacement_source_sha256": replacement_source_sha256,
            "correction_request_sha256": None,
        }

    def trusted_correction_context(self, replacement_source_sha256):
        return {
            "superseded_import": {
                "import_id": "import-one",
                "source_sha256": "sha256:" + "a" * 64,
            },
            "replacement_source": {
                "source_manifest_id": "source-manifest-two",
                "source_sha256": replacement_source_sha256,
            },
        }

    def test_correction_request_accepts_distinct_trusted_replacement_source(self):
        replacement_source_sha256 = "sha256:" + "b" * 64
        sealed = validate_correction_request(
            self.correction_request(replacement_source_sha256),
            trusted_correction_context=self.trusted_correction_context(
                replacement_source_sha256
            ),
        )
        self.assertEqual(replacement_source_sha256, sealed["replacement_source_sha256"])

    def test_correction_request_rejects_mismatched_superseded_import_identity(self):
        replacement_source_sha256 = "sha256:" + "b" * 64
        with self.assertRaisesRegex(ContractError, "superseded import identity"):
            validate_correction_request(
                self.correction_request(replacement_source_sha256),
                trusted_correction_context={
                    **self.trusted_correction_context(replacement_source_sha256),
                    "superseded_import": {
                        "import_id": "import-two",
                        "source_sha256": "sha256:" + "a" * 64,
                    },
                },
            )

    def test_correction_request_rejects_mismatched_replacement_snapshot(self):
        replacement_source_sha256 = "sha256:" + "b" * 64
        with self.assertRaisesRegex(ContractError, "trusted replacement source snapshot"):
            validate_correction_request(
                self.correction_request("sha256:" + "c" * 64),
                trusted_correction_context=self.trusted_correction_context(
                    replacement_source_sha256
                ),
            )
