"""Closed synthetic-platform adapter conformance tests."""

from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "skills" / "audience-panel-builder" / "scripts"))

from audience_panel_builder.common import ContractError  # noqa: E402
from audience_panel_builder.population.experimental_calibration.adapters import (  # noqa: E402
    normalize_platform_export,
)
from audience_panel_builder.population.experimental_calibration.attributes import (  # noqa: E402
    build_creative_attribute_registry,
)
from conformance.experimental_calibration_fixtures import (  # noqa: E402
    creative_attribute_inputs,
    raw_platform_export_fixture,
)


def _digest_bytes(value: bytes) -> str:
    return "sha256:" + hashlib.sha256(value).hexdigest()


class ExperimentalCalibrationAdapterTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.manifest = json.loads(
            (
                ROOT
                / "conformance/fixtures/experimental-calibration/study-manifest.json"
            ).read_text()
        )
        cls.registry = build_creative_attribute_registry(**creative_attribute_inputs())

    def normalize(self, platform: str, raw: bytes | None = None):
        raw = raw if raw is not None else raw_platform_export_fixture(platform)
        return normalize_platform_export(
            platform=platform,
            raw_export_bytes=raw,
            source_sha256=_digest_bytes(raw),
            study_manifest=self.manifest,
            creative_attribute_registry=self.registry,
        )

    def test_meta_typed_actions_values_outbound_and_nonadditive_reach_survive(self):
        row = self.normalize("meta")[0]
        self.assertNotEqual(row["traffic"]["clicks_all"], row["traffic"]["outbound_clicks"])
        self.assertEqual("non_additive_estimated", row["delivery"]["reach_status"])
        self.assertTrue(any(item["event_kind"] == "action_value" for item in row["outcome_events"]))
        self.assertTrue(any(item["metric_id"] == "landing_page_view" for item in row["outcome_events"]))

    def test_google_cost_micros_fractional_conversions_and_date_basis_survive(self):
        row = next(
            row
            for row in self.normalize("google")
            if isinstance(
                next(
                    item["count"]
                    for item in row["outcome_events"]
                    if item["metric_id"] == "conversions"
                ),
                float,
            )
        )
        self.assertEqual(row["delivery"]["spend_micros"] / 1_000_000, row["delivery"]["spend"])
        conversion = next(item for item in row["outcome_events"] if item["metric_id"] == "conversions")
        self.assertIsInstance(conversion["count"], float)
        self.assertNotEqual(row["traffic"]["clicks_all"], row["traffic"]["interactions"])
        self.assertNotEqual(
            conversion["count"],
            next(item for item in row["outcome_events"] if item["metric_id"] == "all_conversions")["count"],
        )
        self.assertNotEqual(
            row["measurement_definition"]["interaction_date"],
            row["measurement_definition"]["conversion_date"],
        )
        self.assertEqual("modeled_and_observed", row["measurement_definition"]["data_status"])

    def test_linkedin_click_sends_conversion_and_suppression_semantics_survive(self):
        rows = self.normalize("linkedin")
        row = rows[0]
        self.assertNotEqual(row["traffic"]["chargeable_clicks"], row["traffic"]["landing_page_clicks"])
        self.assertEqual("sponsored-messaging-delivery", row["delivery"]["sends_semantics"])
        metrics = {item["metric_id"] for item in row["outcome_events"]}
        self.assertTrue({"post_click_conversions", "post_view_conversions"} <= metrics)
        suppressed = next(item for item in rows if item["completeness"]["metric_state"] == "suppressed")
        self.assertIsNone(
            next(
                item["count"]
                for item in suppressed["outcome_events"]
                if item["metric_id"] == "total_conversions"
            )
        )

    def test_tiktok_click_types_attribution_video_and_denominators_survive(self):
        row = self.normalize("tiktok")[0]
        self.assertNotEqual(row["traffic"]["clicks_all"], row["traffic"]["destination_clicks"])
        denominator_kinds = {
            item["metric_id"]: item["denominator_kind"]
            for item in row["denominators"]
        }
        self.assertEqual("clicks_all", denominator_kinds["cvr-all-clicks"])
        self.assertEqual(
            "destination_clicks",
            denominator_kinds["cvr-destination-click"],
        )
        attribution = {
            item["attribution_kind"]
            for item in row["outcome_events"]
            if item["metric_id"].endswith("conversions")
        }
        self.assertEqual({"cta", "vta", "evta"}, attribution)
        self.assertTrue(
            {"video_p25", "video_p50", "video_p75", "video_p100"}
            <= set(row["delivery"]["video_metrics"])
        )
        rates = {
            item["metric_id"]: item
            for item in row["measurement_definition"]["rates"]
        }
        self.assertEqual(
            "impressions",
            rates["cvr-impression"]["denominator_kind"],
        )
        self.assertEqual(
            "destination_clicks",
            rates["cvr-destination-click"]["denominator_kind"],
        )

    def test_native_attribution_cost_delay_and_report_time_are_preserved(self):
        meta = self.normalize("meta")[0]
        self.assertEqual(
            meta["measurement_definition"]["action_report_time"],
            meta["measurement_definition"]["attribution_report_time"],
        )
        google = self.normalize("google")[0]
        self.assertEqual(
            "data-driven",
            google["measurement_definition"]["attribution_model"],
        )
        linkedin = self.normalize("linkedin")[0]
        self.assertEqual(
            2,
            linkedin["measurement_definition"]["reporting_delay_days"],
        )
        self.assertEqual(
            linkedin["delivery"]["spend_local"],
            linkedin["delivery"]["spend_usd"],
        )
        self.assertEqual(
            "last-touch",
            linkedin["measurement_definition"]["attribution_model"],
        )

    def test_malformed_linkedin_reporting_delay_is_rejected(self):
        document = json.loads(raw_platform_export_fixture("linkedin"))
        document["rows"][0]["reporting_delay_days"] = True
        raw = (
            json.dumps(document, sort_keys=True, separators=(",", ":")) + "\n"
        ).encode()
        with self.assertRaisesRegex(ContractError, "reporting_delay_days"):
            self.normalize("linkedin", raw)

    def test_hypotheses_bind_only_current_creative_values(self):
        inputs = creative_attribute_inputs()
        inputs["creative_attributes"] = [
            item
            for item in inputs["creative_attributes"]
            if not (
                item["creative_id"] == "quantified-payback"
                and item["attribute_id"] == "quantified-payback-proof"
            )
        ]
        registry = build_creative_attribute_registry(**inputs)
        raw = raw_platform_export_fixture("meta")
        rows = normalize_platform_export(
            platform="meta",
            raw_export_bytes=raw,
            source_sha256=_digest_bytes(raw),
            study_manifest=self.manifest,
            creative_attribute_registry=registry,
        )
        quantified = next(
            row
            for row in rows
            if row["creative_binding"]["creative_id"] == "quantified-payback"
        )
        self.assertEqual(
            [],
            quantified["creative_attribute_binding"]["hypothesis_ids"],
        )
        self.assertEqual(
            ["dominant-background-color"],
            [
                item["attribute_id"]
                for item in quantified["creative_attribute_binding"]["attributes"]
            ],
        )

    def test_objective_only_registry_normalizes_with_empty_hypothesis_set(self):
        inputs = creative_attribute_inputs()
        inputs["attribute_definitions"] = [
            item
            for item in inputs["attribute_definitions"]
            if item["attribute_kind"] == "objective"
        ]
        inputs["creative_attributes"] = [
            item
            for item in inputs["creative_attributes"]
            if item["attribute_id"] == "dominant-background-color"
        ]
        registry = build_creative_attribute_registry(**inputs)
        raw = raw_platform_export_fixture("meta")
        rows = normalize_platform_export(
            platform="meta",
            raw_export_bytes=raw,
            source_sha256=_digest_bytes(raw),
            study_manifest=self.manifest,
            creative_attribute_registry=registry,
        )
        self.assertTrue(
            all(
                row["creative_attribute_binding"]["hypothesis_ids"] == []
                for row in rows
            )
        )

    def test_missing_zero_suppressed_and_omitted_zero_remain_distinct(self):
        states = {
            row["completeness"]["metric_state"]
            for row in self.normalize("linkedin")
        }
        self.assertTrue({"observed", "zero", "missing", "suppressed", "omitted-zero"} <= states)

    def test_exact_source_digest_is_verified_before_parse(self):
        raw = b"{not-json"
        with self.assertRaisesRegex(ContractError, "source_sha256"):
            normalize_platform_export(
                platform="meta",
                raw_export_bytes=raw,
                source_sha256="sha256:" + "0" * 64,
                study_manifest=self.manifest,
                creative_attribute_registry=self.registry,
            )

    def test_unknown_platform_and_unknown_raw_field_fail_closed(self):
        with self.assertRaisesRegex(ContractError, "platform"):
            self.normalize("snapchat", raw_platform_export_fixture("meta"))
        document = json.loads(raw_platform_export_fixture("meta"))
        document["rows"][0]["helpful_extension"] = True
        raw = (json.dumps(document, sort_keys=True, separators=(",", ":")) + "\n").encode()
        with self.assertRaisesRegex(ContractError, "unknown fields"):
            self.normalize("meta", raw)

    def test_manifest_registry_and_creative_bindings_are_authenticated(self):
        manifest = deepcopy(self.manifest)
        manifest["study_id"] = "other-study"
        raw = raw_platform_export_fixture("meta")
        with self.assertRaisesRegex(ContractError, "manifest_sha256"):
            normalize_platform_export(
                platform="meta",
                raw_export_bytes=raw,
                source_sha256=_digest_bytes(raw),
                study_manifest=manifest,
                creative_attribute_registry=self.registry,
            )
        registry = deepcopy(self.registry)
        registry["registered_at"] = "2026-07-01T00:00:01Z"
        with self.assertRaisesRegex(ContractError, "registry_sha256"):
            normalize_platform_export(
                platform="meta",
                raw_export_bytes=raw,
                source_sha256=_digest_bytes(raw),
                study_manifest=self.manifest,
                creative_attribute_registry=registry,
            )

    def test_currency_timezone_and_overlapping_breakdowns_fail(self):
        for field, replacement, expected in (
            ("currency", "EUR", "currency"),
            ("timezone", "America/New_York", "timezone"),
            ("breakdown_overlap_permitted", True, "overlap"),
        ):
            with self.subTest(field=field):
                document = json.loads(raw_platform_export_fixture("meta"))
                document["reporting_context"][field] = replacement
                raw = (json.dumps(document, sort_keys=True, separators=(",", ":")) + "\n").encode()
                with self.assertRaisesRegex(ContractError, expected):
                    self.normalize("meta", raw)

    def test_duplicate_grouping_identity_fails(self):
        document = json.loads(raw_platform_export_fixture("meta"))
        document["rows"].append(deepcopy(document["rows"][0]))
        raw = (json.dumps(document, sort_keys=True, separators=(",", ":")) + "\n").encode()
        with self.assertRaisesRegex(ContractError, "duplicate observation"):
            self.normalize("meta", raw)

    def test_observation_ids_are_globally_scoped_to_scenario_and_source(self):
        ids_by_scenario = {}
        for scenario_id in (
            "known-proof-need-miss",
            "null-effect",
            "non-identifiable-twin-a",
            "non-identifiable-twin-b",
        ):
            raw = raw_platform_export_fixture("google", scenario_id)
            ids_by_scenario[scenario_id] = {
                row["observation_id"] for row in self.normalize("google", raw)
            }
        scenarios = list(ids_by_scenario)
        for index, left in enumerate(scenarios):
            for right in scenarios[index + 1 :]:
                self.assertFalse(ids_by_scenario[left] & ids_by_scenario[right])

    def test_primary_metric_marker_must_equal_native_field(self):
        document = json.loads(raw_platform_export_fixture("google"))
        document["rows"][0]["metric_reporting_state"]["value"] = 999999.25
        raw = (
            json.dumps(document, sort_keys=True, separators=(",", ":")) + "\n"
        ).encode()
        with self.assertRaisesRegex(ContractError, "native metric"):
            self.normalize("google", raw)

    def test_meta_native_click_marker_cannot_relabel_outbound_primary_event(self):
        document = json.loads(raw_platform_export_fixture("meta"))
        document["rows"][0]["metric_reporting_state"] = {
            "metric": "clicks",
            "state": "observed",
            "value": document["rows"][0]["clicks"],
        }
        raw = (
            json.dumps(document, sort_keys=True, separators=(",", ":")) + "\n"
        ).encode()
        with self.assertRaisesRegex(ContractError, "approved primary metric"):
            self.normalize("meta", raw)

    def test_each_adapter_rejects_nondefault_native_primary_metric(self):
        alternatives = {
            "google": "all_conversions",
            "linkedin": "post_click_conversions",
            "tiktok": "vta_conversions",
        }
        for platform, alternative in alternatives.items():
            with self.subTest(platform=platform):
                document = json.loads(raw_platform_export_fixture(platform))
                document["rows"][0]["metric_reporting_state"] = {
                    "metric": alternative,
                    "state": "observed",
                    "value": document["rows"][0][alternative],
                }
                raw = (
                    json.dumps(
                        document,
                        sort_keys=True,
                        separators=(",", ":"),
                    )
                    + "\n"
                ).encode()
                with self.assertRaisesRegex(
                    ContractError,
                    "approved primary metric",
                ):
                    self.normalize(platform, raw)

    def test_public_observation_rejects_resealed_primary_relabel_per_platform(self):
        alternatives = {
            "meta": "lead",
            "google": "all_conversions",
            "linkedin": "post_click_conversions",
            "tiktok": "vta_conversions",
        }
        for platform, alternative in alternatives.items():
            with self.subTest(platform=platform):
                row = next(
                    deepcopy(item)
                    for item in self.normalize(platform)
                    if item["completeness"]["metric_state"] == "observed"
                    and any(
                        event["metric_id"] == alternative
                        and event["data_status"] == "observed"
                        for event in item["outcome_events"]
                    )
                )
                alternative_event = next(
                    event
                    for event in row["outcome_events"]
                    if event["metric_id"] == alternative
                )
                row["measurement_definition"]["primary_metric_id"] = (
                    alternative
                )
                row["completeness"]["metric_state"] = alternative_event[
                    "data_status"
                ]
                row["completeness"]["row_state"] = alternative_event[
                    "data_status"
                ]
                row["denominators"].append(
                    {
                        "metric_id": alternative,
                        "denominator_kind": "impressions",
                        "denominator_value": row["delivery"]["impressions"],
                    }
                )
                with self.assertRaisesRegex(
                    ContractError,
                    "approved primary metric",
                ):
                    self._validate_resealed_observation(row)

    def _validate_resealed_observation(self, observation):
        from audience_panel_builder.population.experimental_calibration.contracts import (
            validate_outcome_observation,
        )
        from conformance.experimental_calibration_fixtures import rehash

        return validate_outcome_observation(
            rehash(observation, "observation_sha256")
        )

    def _observed_observation(self, platform):
        return next(
            deepcopy(item)
            for item in self.normalize(platform)
            if item["completeness"]["metric_state"] == "observed"
        )

    def test_primary_denominator_replacement_is_rejected_per_platform(self):
        for platform in ("meta", "google", "linkedin", "tiktok"):
            with self.subTest(platform=platform):
                row = self._observed_observation(platform)
                primary_id = row["measurement_definition"][
                    "primary_metric_id"
                ]
                primary = next(
                    item
                    for item in row["denominators"]
                    if item["metric_id"] == primary_id
                )
                primary.update(
                    {
                        "denominator_kind": "invented_count",
                        "denominator_value": 1,
                    }
                )
                with self.assertRaisesRegex(
                    ContractError,
                    "approved primary denominator",
                ):
                    self._validate_resealed_observation(row)

    def test_extra_event_denominator_is_rejected_per_platform(self):
        extra_events = {
            "meta": "lead",
            "google": "all_conversions",
            "linkedin": "post_click_conversions",
            "tiktok": "vta_conversions",
        }
        for platform, metric_id in extra_events.items():
            with self.subTest(platform=platform):
                row = self._observed_observation(platform)
                row["denominators"].append(
                    {
                        "metric_id": metric_id,
                        "denominator_kind": "invented_count",
                        "denominator_value": 1,
                    }
                )
                with self.assertRaisesRegex(
                    ContractError,
                    "complete approved set",
                ):
                    self._validate_resealed_observation(row)

    def test_primary_denominator_invented_value_is_rejected_per_platform(self):
        for platform in ("meta", "google", "linkedin", "tiktok"):
            with self.subTest(platform=platform):
                row = self._observed_observation(platform)
                primary_id = row["measurement_definition"][
                    "primary_metric_id"
                ]
                next(
                    item
                    for item in row["denominators"]
                    if item["metric_id"] == primary_id
                )["denominator_value"] = row["delivery"]["impressions"] + 1
                with self.assertRaisesRegex(
                    ContractError,
                    "canonical source field",
                ):
                    self._validate_resealed_observation(row)

    def test_primary_denominator_missing_duplicate_and_type_drift_fail(self):
        mutations = (
            (
                lambda row, primary_id: row.update(
                    {
                        "denominators": [
                            item
                            for item in row["denominators"]
                            if item["metric_id"] != primary_id
                        ]
                    }
                ),
                "complete approved set",
            ),
            (
                lambda row, primary_id: row["denominators"].append(
                    deepcopy(
                        next(
                            item
                            for item in row["denominators"]
                            if item["metric_id"] == primary_id
                        )
                    )
                ),
                "unique",
            ),
            (
                lambda row, primary_id: next(
                    item
                    for item in row["denominators"]
                    if item["metric_id"] == primary_id
                ).update(
                    {
                        "denominator_value": float(
                            row["delivery"]["impressions"]
                        )
                    }
                ),
                "type-exact primary denominator",
            ),
        )
        for mutate, expected in mutations:
            with self.subTest(expected=expected):
                row = self._observed_observation("google")
                primary_id = row["measurement_definition"][
                    "primary_metric_id"
                ]
                mutate(row, primary_id)
                with self.assertRaisesRegex(ContractError, expected):
                    self._validate_resealed_observation(row)

    def _observed_tiktok_observation(self):
        return self._observed_observation("tiktok")

    def test_tiktok_impossible_rate_value_fails_recomputation(self):
        row = self._observed_tiktok_observation()
        row["measurement_definition"]["rates"][0]["rate_value"] = 999.0
        with self.assertRaisesRegex(ContractError, "frozen rate"):
            self._validate_resealed_observation(row)

    def test_tiktok_rate_status_must_equal_numerator_event_state(self):
        row = self._observed_tiktok_observation()
        row["measurement_definition"]["rates"][0]["data_status"] = "estimated"
        with self.assertRaisesRegex(ContractError, "numerator event state"):
            self._validate_resealed_observation(row)

    def test_tiktok_rate_requires_its_exact_denominator_entry(self):
        row = self._observed_tiktok_observation()
        rate_id = row["measurement_definition"]["rates"][0]["metric_id"]
        row["denominators"] = [
            item
            for item in row["denominators"]
            if item["metric_id"] != rate_id
        ]
        with self.assertRaisesRegex(ContractError, "exactly one denominator"):
            self._validate_resealed_observation(row)

    def test_missing_tiktok_numerator_cannot_publish_observed_rate(self):
        row = next(
            deepcopy(item)
            for item in self.normalize("tiktok")
            if item["completeness"]["metric_state"] == "missing"
        )
        row["measurement_definition"]["rates"][0].update(
            {"data_status": "observed", "rate_value": 0.5}
        )
        with self.assertRaisesRegex(ContractError, "numerator event state"):
            self._validate_resealed_observation(row)

    def test_tiktok_rate_wrong_metric_identity_and_zero_denominator_fail(self):
        mutations = [
            (
                lambda row: row["denominators"][0].update(
                    {"metric_id": "vta_conversions"}
                ),
                "exactly one denominator",
            ),
            (
                lambda row: (
                    row["measurement_definition"]["rates"][0].update(
                        {"denominator_value": 0, "rate_value": 0}
                    ),
                    row["denominators"][0].update({"denominator_value": 0}),
                ),
                "strictly positive",
            ),
            (
                lambda row: row["denominators"].append(
                    deepcopy(row["denominators"][0])
                ),
                "unique",
            ),
        ]
        for mutate, expected in mutations:
            with self.subTest(expected=expected):
                row = self._observed_tiktok_observation()
                mutate(row)
                with self.assertRaisesRegex(ContractError, expected):
                    self._validate_resealed_observation(row)

    def test_tiktok_rate_rejects_invented_and_unknown_identity(self):
        mutations = [
            (
                lambda row, rate, denominator: (
                    rate.update(
                        {
                            "denominator_kind": "invented_count",
                            "denominator_value": 1,
                            "rate_value": rate["numerator_value"] / 1,
                        }
                    ),
                    denominator.update(
                        {
                            "denominator_kind": "invented_count",
                            "denominator_value": 1,
                        }
                    ),
                ),
                "approved denominator",
            ),
            (
                lambda row, rate, denominator: (
                    rate.update({"metric_id": "cvr-aaa"}),
                    denominator.update({"metric_id": "cvr-aaa"}),
                ),
                "approved rate",
            ),
            (
                lambda row, rate, denominator: (
                    rate.update(
                        {
                            "numerator_metric_id": "vta_conversions",
                            "numerator_value": next(
                                event["count"]
                                for event in row["outcome_events"]
                                if event["metric_id"] == "vta_conversions"
                            ),
                        }
                    ),
                    rate.update(
                        {
                            "rate_value": round(
                                rate["numerator_value"]
                                / rate["denominator_value"],
                                8,
                            )
                        }
                    ),
                ),
                "approved numerator",
            ),
        ]
        for mutate, expected in mutations:
            with self.subTest(expected=expected):
                row = self._observed_tiktok_observation()
                rate = row["measurement_definition"]["rates"][0]
                denominator = next(
                    item
                    for item in row["denominators"]
                    if item["metric_id"] == rate["metric_id"]
                )
                mutate(row, rate, denominator)
                with self.assertRaisesRegex(ContractError, expected):
                    self._validate_resealed_observation(row)

    def test_tiktok_rate_rejects_source_and_numeric_type_drift(self):
        mutations = [
            (
                lambda row, rate, denominator: rate.update(
                    {"numerator_value": float(rate["numerator_value"])}
                ),
                "type-exact numerator",
            ),
            (
                lambda row, rate, denominator: (
                    rate.update(
                        {
                            "denominator_value": float(
                                rate["denominator_value"]
                            )
                        }
                    ),
                    denominator.update(
                        {
                            "denominator_value": float(
                                denominator["denominator_value"]
                            )
                        }
                    ),
                ),
                "type-exact denominator",
            ),
            (
                lambda row, rate, denominator: row["traffic"].update(
                    {
                        "clicks_all": row["traffic"]["clicks_all"] + 1,
                    }
                ),
                "canonical source field",
            ),
        ]
        for mutate, expected in mutations:
            with self.subTest(expected=expected):
                row = self._observed_tiktok_observation()
                rate = row["measurement_definition"]["rates"][0]
                denominator = next(
                    item
                    for item in row["denominators"]
                    if item["metric_id"] == rate["metric_id"]
                )
                mutate(row, rate, denominator)
                with self.assertRaisesRegex(ContractError, expected):
                    self._validate_resealed_observation(row)

    def test_non_tiktok_platforms_forbid_resealed_rates(self):
        for platform in ("meta", "google", "linkedin"):
            with self.subTest(platform=platform):
                row = next(
                    deepcopy(item)
                    for item in self.normalize(platform)
                    if item["completeness"]["metric_state"] == "observed"
                )
                primary_id = row["measurement_definition"][
                    "primary_metric_id"
                ]
                primary_event = next(
                    event
                    for event in row["outcome_events"]
                    if event["metric_id"] == primary_id
                )
                rate_id = "invented-rate"
                row["measurement_definition"]["rates"] = [
                    {
                        "metric_id": rate_id,
                        "rate_value": round(
                            primary_event["count"]
                            / row["delivery"]["impressions"],
                            8,
                        ),
                        "numerator_metric_id": primary_id,
                        "numerator_value": primary_event["count"],
                        "denominator_kind": "impressions",
                        "denominator_value": row["delivery"]["impressions"],
                        "data_status": primary_event["data_status"],
                    }
                ]
                row["denominators"].append(
                    {
                        "metric_id": rate_id,
                        "denominator_kind": "impressions",
                        "denominator_value": row["delivery"]["impressions"],
                    }
                )
                with self.assertRaisesRegex(
                    ContractError,
                    "does not allow rates",
                ):
                    self._validate_resealed_observation(row)

    def test_linkedin_observed_row_cannot_claim_low_volume_suppression(self):
        document = json.loads(raw_platform_export_fixture("linkedin"))
        self.assertEqual("observed", document["rows"][0]["row_state"])
        document["rows"][0]["suppression_status"] = "suppressed-low-volume"
        raw = (
            json.dumps(document, sort_keys=True, separators=(",", ":")) + "\n"
        ).encode()
        with self.assertRaisesRegex(
            ContractError,
            "suppression_status must match row_state",
        ):
            self.normalize("linkedin", raw)
        observed = deepcopy(self.normalize("linkedin")[0])
        observed["completeness"]["suppression_status"] = (
            "suppressed-low-volume"
        )
        with self.assertRaisesRegex(
            ContractError,
            "LinkedIn suppression_status",
        ):
            self._validate_resealed_observation(observed)
        suppressed = next(
            deepcopy(item)
            for item in self.normalize("linkedin")
            if item["completeness"]["metric_state"] == "suppressed"
        )
        suppressed["completeness"]["suppression_status"] = "not-suppressed"
        with self.assertRaisesRegex(
            ContractError,
            "LinkedIn suppression_status",
        ):
            self._validate_resealed_observation(suppressed)

    def test_canonical_event_kind_state_and_denominator_contradictions_fail(self):
        from audience_panel_builder.population.experimental_calibration.contracts import (
            validate_outcome_observation,
        )
        from conformance.experimental_calibration_fixtures import rehash

        row = self.normalize("google")[0]
        mutations = [
            (
                lambda value: value["outcome_events"][0].update(
                    {"count": None, "value": 2.0}
                ),
                "event_kind",
            ),
            (
                lambda value: value["outcome_events"][0].update(
                    {"count": None, "data_status": "observed"}
                ),
                "non-null",
            ),
            (
                lambda value: value["denominators"][0].update(
                    {"metric_id": "invented-rate"}
                ),
                "emitted metric or rate",
            ),
        ]
        for mutate, expected in mutations:
            with self.subTest(expected=expected):
                changed = deepcopy(row)
                mutate(changed)
                with self.assertRaisesRegex(ContractError, expected):
                    validate_outcome_observation(
                        rehash(changed, "observation_sha256")
                    )

    def test_secondary_linkedin_events_do_not_inherit_primary_missing_state(self):
        row = next(
            item
            for item in self.normalize("linkedin")
            if item["completeness"]["metric_state"] == "missing"
        )
        statuses = {
            item["metric_id"]: item["data_status"]
            for item in row["outcome_events"]
        }
        self.assertEqual("missing", statuses["total_conversions"])
        self.assertIn(
            statuses["post_click_conversions"],
            {"observed", "estimated"},
        )
        self.assertIn(
            statuses["post_view_conversions"],
            {"observed", "estimated"},
        )

    def test_output_is_deterministic_under_reversed_rows_and_inputs_unchanged(self):
        raw_document = json.loads(raw_platform_export_fixture("google"))
        reversed_document = deepcopy(raw_document)
        reversed_document["rows"] = list(reversed(reversed_document["rows"]))
        raw_reversed = (
            json.dumps(reversed_document, sort_keys=True, separators=(",", ":")) + "\n"
        ).encode()
        manifest = deepcopy(self.manifest)
        registry = deepcopy(self.registry)
        first = self.normalize("google", raw_reversed)
        replay = self.normalize("google", raw_reversed)
        second = self.normalize("google")
        self.assertEqual(first, replay)
        self.assertEqual(
            {
                tuple(sorted(row["experiment_binding"].items()))
                for row in first
            },
            {
                tuple(sorted(row["experiment_binding"].items()))
                for row in second
            },
        )
        self.assertEqual(self.manifest, manifest)
        self.assertEqual(self.registry, registry)

    def test_import_cli_publishes_once_and_refuses_existing_output(self):
        cli = (
            ROOT
            / "skills/audience-panel-builder/scripts/import-synthetic-platform-outcomes.py"
        )
        raw_path = (
            ROOT
            / "conformance/fixtures/experimental-calibration/open/"
            "known-proof-need-miss/raw/google/daily-aggregates.json"
        )
        raw = raw_path.read_bytes()
        with tempfile.TemporaryDirectory() as raw_temp:
            temp = Path(raw_temp)
            manifest_path = temp / "study.json"
            registry_path = temp / "registry.json"
            output_path = temp / "observations.json"
            manifest_path.write_text(json.dumps(self.manifest))
            registry_path.write_text(json.dumps(self.registry))
            command = [
                sys.executable,
                str(cli),
                "--platform",
                "google",
                "--input",
                str(raw_path),
                "--source-sha256",
                _digest_bytes(raw),
                "--study-manifest",
                str(manifest_path),
                "--attribute-registry",
                str(registry_path),
                "--output",
                str(output_path),
            ]
            first = subprocess.run(command, capture_output=True, check=False)
            self.assertEqual(0, first.returncode, first.stderr.decode())
            self.assertTrue(json.loads(output_path.read_text()))
            before = output_path.read_bytes()
            existing = subprocess.run(command, capture_output=True, check=False)
            self.assertEqual(3, existing.returncode)
            self.assertEqual(before, output_path.read_bytes())
            target = temp / "target"
            target.mkdir()
            linked_parent = temp / "linked-parent"
            linked_parent.symlink_to(target, target_is_directory=True)
            aliased = subprocess.run(
                command[:-1] + [str(linked_parent / "observations.json")],
                capture_output=True,
                check=False,
            )
            self.assertEqual(3, aliased.returncode)
            self.assertFalse((target / "observations.json").exists())


if __name__ == "__main__":
    unittest.main()
