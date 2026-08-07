from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SKILL = ROOT / "skills" / "audience-data-lab"
SCRIPTS = SKILL / "scripts"
AD_TESTING_SCRIPTS = ROOT / "skills" / "audience-ad-testing-lab" / "scripts"
PANEL_BUILDER_SCRIPTS = ROOT / "skills" / "audience-panel-builder" / "scripts"
FIXTURES = ROOT / "conformance" / "fixtures" / "authorized-audience"
SHAPES = FIXTURES / "source-shapes"
for scripts_path in (SCRIPTS, AD_TESTING_SCRIPTS, PANEL_BUILDER_SCRIPTS):
    sys.path.insert(0, str(scripts_path))

from audience_data_lab.authorized_source import profile_authorized_bundle  # noqa: E402
from audience_data_lab.authorized_mapping import (  # noqa: E402
    ALLOWED_OPERATIONS,
    AUTHORIZED_MAPPING_VERSION,
    SEMANTIC_ROUTES,
    mapping_sha256,
    validate_authorized_mapping,
)
from audience_data_lab.authorized_transform import (  # noqa: E402
    AUTHORIZED_HANDOFF_VERSION,
    TRANSFORMATION_REPORT_VERSION,
    _snapshot_input,
    apply_authorized_operations,
    transform_authorized_bundle,
    validate_authorized_handoff,
    validate_transformation_report,
)
from audience_data_lab.common import (  # noqa: E402
    ContractError,
    canonical_json_bytes,
    sha256_bytes,
    sha256_file,
    write_new_bytes,
)
from audience_lab.audience_research_v3 import (  # noqa: E402
    validate_observation_batch,
    validate_outcome_feedback,
)
from audience_panel_builder.evidence import build_evidence_ledger  # noqa: E402


class AuthorizedAudienceTransformTests(unittest.TestCase):
    def setUp(self):
        self._temporary = tempfile.TemporaryDirectory()
        self.root = Path(self._temporary.name)
        self.source = self.root / "source"
        self.source.mkdir()
        for name in ("flat-structural.csv",):
            (self.source / name).write_bytes((SHAPES / name).read_bytes())
        for name in ("topic-social-evidence.json", "aggregate-outcomes.json"):
            (self.source / name).write_bytes((FIXTURES / name).read_bytes())
        self.profile = self._profile(
            self.source / "flat-structural.csv",
            self.source / "topic-social-evidence.json",
            self.source / "aggregate-outcomes.json",
        )

    def tearDown(self):
        self._temporary.cleanup()

    def _profile(self, *paths: Path) -> dict[str, object]:
        return profile_authorized_bundle(
            list(paths),
            profile_id="fictional-marketplace-cohort",
            profile_version="1.0.0",
            profiled_at="2026-07-24T12:00:00Z",
        )

    def _selection(
        self,
        selection_id: str,
        file_name: str,
        fields: list[str],
        *,
        record_path: str = "$",
        sheet: str | None = None,
        unit: str,
        denominator: str,
        join_keys: list[str] | None = None,
    ) -> dict[str, object]:
        digest = next(
            item["sha256"]
            for item in self.profile["inputs"]
            if item["display_name"] == file_name
        )
        return {
            "selection_id": selection_id,
            "file": file_name,
            "file_sha256": digest,
            "sheet": sheet,
            "record_path": record_path,
            "fields": fields,
            "unit": unit,
            "denominator": denominator,
            "aggregate_join_keys": join_keys or [],
        }

    def _complete_field_routes(
        self,
        mapping: dict[str, object],
        source_routes: dict[str, str],
    ) -> list[dict[str, str]]:
        datasets: dict[str, dict[str, str]] = {}
        ordered: list[tuple[str, list[str]]] = []
        for selection in mapping["selections"]:
            name = selection["selection_id"]
            route = source_routes[name]
            datasets[name] = {field: route for field in selection["fields"]}
            ordered.append((name, list(selection["fields"])))
        for operation in mapping["operations"]:
            op = operation["op"]
            output = operation["output"]
            if op == "join":
                routes = {
                    **datasets[operation["left"]],
                    **datasets[operation["right"]],
                }
            else:
                routes = dict(datasets[operation["input"]])
                if op == "select":
                    routes = {field: routes[field] for field in operation["fields"]}
                elif op == "rename":
                    routes = {
                        operation["fields"].get(field, field): route
                        for field, route in routes.items()
                    }
                elif op == "flatten":
                    routes = {
                        field: datasets[operation["input"]][path.split(".", 1)[0]]
                        for field, path in operation["fields"].items()
                    }
                elif op == "wide_to_long":
                    value_routes = {
                        routes[field] for field in operation["value_fields"]
                    }
                    self.assertEqual(1, len(value_routes))
                    value_route = next(iter(value_routes))
                    routes = {
                        **{field: routes[field] for field in operation["id_fields"]},
                        operation["name_field"]: value_route,
                        operation["value_field"]: value_route,
                    }
                elif op == "pivot":
                    value_route = routes[operation["value_field"]]
                    routes = {
                        **{field: routes[field] for field in operation["index_fields"]},
                        **{field: value_route for field in operation["columns"]},
                    }
                elif op == "normalize_suppression":
                    routes[operation["status_field"]] = routes[operation["field"]]
                elif op in {"derive_share", "normalize_weight"}:
                    routes[operation["output_field"]] = "structural_frame"
                elif op == "aggregate":
                    input_routes = datasets[operation["input"]]
                    routes = {
                        field: input_routes[field] for field in operation["group_by"]
                    }
                    for field, metric in operation["metrics"].items():
                        routes[field] = input_routes[metric["field"]]
            datasets[output] = routes
            ordered.append((output, list(routes)))
        return [
            {"dataset": dataset, "field": field, "route": datasets[dataset][field]}
            for dataset, fields in ordered
            for field in fields
        ]

    def _frame_metadata(self) -> dict[str, object]:
        return {
            "batch_id": "fictional-marketplace-frame-observations",
            "frame_request_id": "fictional-marketplace-frame",
            "adapter_id": "authorized-audience-data-lab",
            "source_family": "authorized-aggregate",
            "source": {
                "publisher": "Fictional Marketplace Research",
                "program": "Authorized cohort census",
                "edition": "2026 approved export",
                "vintage": "2026-07-24",
                "retrieved_at": "2026-07-24T12:00:00Z",
            },
            "raw_snapshot_sha256": "sha256:" + "1" * 64,
            "access": {
                "access_type": "authorized",
                "permission_confirmed": True,
                "permitted_uses": [
                    "audience-composition",
                    "population-framing",
                ],
            },
            "geography": ["US"],
            "unit": "eligible-cohort-member",
            "denominator": "all-respondents",
            "dimension_fields": {"cohort": "cohort_id"},
            "estimate_field": "respondent_count",
            "cell_key_field": "cohort_id",
            "cell_metadata": {
                "operations_leaders": {
                    "cell_id": "operations-leaders",
                    "uncertainty": {
                        "lower_field": "respondent_count",
                        "upper_field": "respondent_count",
                        "method": "exact approved aggregate count",
                    },
                    "suppressed": False,
                    "status": "observed",
                    "relationship": "marginal",
                    "source_location": "approved-cohort-export#operations-leaders",
                },
                "finance_leaders": {
                    "cell_id": "finance-leaders",
                    "uncertainty": {
                        "lower_field": "respondent_count",
                        "upper_field": "respondent_count",
                        "method": "exact approved aggregate count",
                    },
                    "suppressed": False,
                    "status": "observed",
                    "relationship": "marginal",
                    "source_location": "approved-cohort-export#finance-leaders",
                },
            },
            "selection_notes": "Approved aggregate cohort counts only.",
            "coverage_notes": "Covers the complete authorized fictional cohort export.",
            "citations": ["Fictional Marketplace Research approved export 2026"],
        }

    def _structured_metadata(self) -> dict[str, object]:
        return {
            "batch_id": "fictional-marketplace-structured-evidence",
            "created_at": "2026-07-24T12:00:00Z",
            "source_adapter": "authorized-audience-data-lab",
            "source_schema_version": "authorized-audience-handoff-v1",
            "input_sha256": "sha256:" + "2" * 64,
            "permission": "allowed",
            "source_status": "complete",
            "item_id_field": "evidence_id",
            "content_summary_field": "summary",
            "item_metadata": {
                "evidence-operations": {
                    "source_url": "https://example.com/community/operations",
                    "item_type": "approved-aggregate-theme",
                    "text_fidelity": "faithful-summary",
                    "content_sha256": "sha256:" + "3" * 64,
                    "source_pointer": "approved-evidence#operations",
                    "upstream_source_ids": ["authorized-cohort-export"],
                    "use_constraints": ["qualitative-overlay-only"],
                    "quality_flags": [],
                },
                "evidence-finance": {
                    "source_url": "https://example.com/community/finance",
                    "item_type": "approved-aggregate-theme",
                    "text_fidelity": "faithful-summary",
                    "content_sha256": "sha256:" + "4" * 64,
                    "source_pointer": "approved-evidence#finance",
                    "upstream_source_ids": ["authorized-cohort-export"],
                    "use_constraints": ["qualitative-overlay-only"],
                    "quality_flags": [],
                },
            },
        }

    def _social_metadata(self) -> dict[str, object]:
        return {
            "batch_id": "fictional-marketplace-social-observations",
            "created_at": "2026-07-24T12:00:00Z",
            "source_adapter": "authorized-audience-data-lab",
            "source_schema_version": "authorized-audience-handoff-v1",
            "input_sha256": "sha256:" + "6" * 64,
            "query": "fictional leaders workflow and cost discussions",
            "window_start": "2026-07-01T00:00:00Z",
            "window_end": "2026-07-24T12:00:00Z",
            "source_status": {"fictional_community": "ok"},
            "collection": {
                "provider": "Fictional Community Research",
                "collector": "authorized-audience-data-lab",
                "collector_version": "1.0.0",
                "run_or_dataset_id": "approved-evidence-2026-07-24",
                "collection_method": "permissioned public social summary export",
                "access_route": "authorized local normalized export",
                "permitted_use": "allowed",
                "sort_mode": "approved source order",
                "item_limit": 2,
                "pagination": "not applicable",
                "completeness": "complete approved summary export",
                "deduplication_control": "source evidence identifier",
                "bot_spam_control": "upstream review completed",
            },
            "coverage_warnings": [
                "Approved qualitative signals do not estimate prevalence."
            ],
            "observation_id_field": "evidence_id",
            "text_excerpt_field": "summary",
            "observation_metadata": {
                "evidence-operations": {
                    "platform": "fictional_community",
                    "source_item_id": "approved-topic-operations",
                    "source_url": "https://example.com/community/operations",
                    "published_at": None,
                    "collected_at": "2026-07-24T12:00:00Z",
                    "unit_of_analysis": "approved_aggregate_theme",
                    "title": "Operations leader theme",
                    "text_fidelity": "faithful_summary",
                    "content_sha256": "sha256:" + "7" * 64,
                    "engagement": {"prevalence_weight": 0},
                    "relevance_score": None,
                    "cluster_id": "operations",
                    "role_status": "unknown",
                    "author_group_token": None,
                    "freshness_verdict": "approved_export",
                    "json_pointer": "/records/0",
                    "use_constraints": [
                        "qualitative_context_only",
                        "no_prevalence_or_weighting",
                    ],
                    "quality_flags": ["summary_not_verbatim"],
                },
                "evidence-finance": {
                    "platform": "fictional_community",
                    "source_item_id": "approved-topic-finance",
                    "source_url": "https://example.com/community/finance",
                    "published_at": None,
                    "collected_at": "2026-07-24T12:00:00Z",
                    "unit_of_analysis": "approved_aggregate_theme",
                    "title": "Finance leader theme",
                    "text_fidelity": "faithful_summary",
                    "content_sha256": "sha256:" + "8" * 64,
                    "engagement": {"prevalence_weight": 0},
                    "relevance_score": None,
                    "cluster_id": "finance",
                    "role_status": "unknown",
                    "author_group_token": None,
                    "freshness_verdict": "approved_export",
                    "json_pointer": "/records/1",
                    "use_constraints": [
                        "qualitative_context_only",
                        "no_prevalence_or_weighting",
                    ],
                    "quality_flags": ["summary_not_verbatim"],
                },
            },
        }

    def _outcome_metadata(
        self,
        *,
        source_cohort: str,
        canonical_cohort: str,
        suffix: str,
    ) -> dict[str, object]:
        return {
            "record_match": {
                "cohort_id": source_cohort,
                "outcome": "qualified_response_rate",
            },
            "feedback_id": f"feedback-{suffix}",
            "panel_id": "fictional-marketplace-panel",
            "study_id": "fictional-marketplace-study",
            "variant_id": "approved-message-a",
            "cohort_id": canonical_cohort,
            "metric": {
                "name": "qualified-response-rate",
                "definition": "Share of eligible cohort members with a qualified response.",
            },
            "metric_direction": "higher_is_better",
            "units": {
                "exposure": "eligible-member",
                "outcome": "qualified-response",
            },
            "windows": {
                "measurement": "2026-07-01 through 2026-07-21",
                "attribution": "Seven days after exposure",
            },
            "aggregate_fields": {
                "numerator": None,
                "denominator": None,
                "value": "value",
            },
            "design": "observational",
            "source": {
                "source_id": "authorized-outcome-export",
                "permission_confirmed": True,
            },
            "holdout": True,
            "missingness": "No missing aggregate values in the selected cohort.",
            "limitations": ["Retrospective aggregate outcome feedback only."],
            "source_sha256": "sha256:" + "5" * 64,
        }

    def _mapping(self) -> dict[str, object]:
        mapping = {
            "schema_version": AUTHORIZED_MAPPING_VERSION,
            "mapping_id": "fictional-marketplace-mapping",
            "mapping_version": "1.0.0",
            "source_profile_sha256": mapping_sha256(self.profile),
            "input_hashes": {
                item["display_name"]: item["sha256"] for item in self.profile["inputs"]
            },
            "selections": [
                self._selection(
                    "frame-source",
                    "flat-structural.csv",
                    ["segment", "respondent_count", "share"],
                    unit="aggregate_cohort",
                    denominator="all_respondents",
                    join_keys=["segment"],
                ),
                self._selection(
                    "evidence-source",
                    "topic-social-evidence.json",
                    ["cohort_id", "evidence_id", "topic", "summary"],
                    record_path="records",
                    unit="aggregate_evidence_item",
                    denominator="selected_evidence_items",
                    join_keys=["cohort_id"],
                ),
                self._selection(
                    "outcome-source",
                    "aggregate-outcomes.json",
                    ["cohort_id", "outcome", "value"],
                    record_path="records",
                    unit="aggregate_cohort_outcome",
                    denominator="eligible_cohort_members",
                    join_keys=["cohort_id"],
                ),
            ],
            "operations": [
                {
                    "operation_id": "rename-frame",
                    "op": "rename",
                    "input": "frame-source",
                    "output": "frame",
                    "fields": {"segment": "cohort_id"},
                },
                {
                    "operation_id": "cast-frame",
                    "op": "cast",
                    "input": "frame",
                    "output": "frame-cast",
                    "fields": {"respondent_count": "integer", "share": "number"},
                },
                {
                    "operation_id": "sort-frame",
                    "op": "sort",
                    "input": "frame-cast",
                    "output": "frame-final",
                    "fields": ["cohort_id"],
                },
                {
                    "operation_id": "sort-evidence",
                    "op": "sort",
                    "input": "evidence-source",
                    "output": "evidence-final",
                    "fields": ["evidence_id"],
                },
                {
                    "operation_id": "sort-social",
                    "op": "sort",
                    "input": "evidence-final",
                    "output": "social-final",
                    "fields": ["evidence_id"],
                },
                {
                    "operation_id": "sort-outcome",
                    "op": "sort",
                    "input": "outcome-source",
                    "output": "outcome-final",
                    "fields": ["cohort_id", "outcome"],
                },
            ],
            "field_routes": [],
            "expected_outputs": [
                {
                    "dataset": "frame-final",
                    "route": "structural_frame",
                    "filename": "frame-observations-0001.json",
                    "schema_version": "audience-frame-observation-batch-v1",
                    "metadata": self._frame_metadata(),
                },
                {
                    "dataset": "evidence-final",
                    "route": "overlay_evidence",
                    "filename": "structured-evidence-0001.json",
                    "schema_version": "audience-structured-evidence-batch-v1",
                    "metadata": self._structured_metadata(),
                },
                {
                    "dataset": "social-final",
                    "route": "overlay_evidence",
                    "filename": "social-observations-0001.json",
                    "schema_version": "social-observation-batch-v1",
                    "metadata": self._social_metadata(),
                },
                {
                    "dataset": "outcome-final",
                    "route": "outcome_feedback",
                    "filename": "outcome-feedback-0001.json",
                    "schema_version": "panel-outcome-feedback-v1",
                    "metadata": self._outcome_metadata(
                        source_cohort="operations_leaders",
                        canonical_cohort="operations-leaders",
                        suffix="operations-leaders",
                    ),
                },
                {
                    "dataset": "outcome-final",
                    "route": "outcome_feedback",
                    "filename": "outcome-feedback-0002.json",
                    "schema_version": "panel-outcome-feedback-v1",
                    "metadata": self._outcome_metadata(
                        source_cohort="finance_leaders",
                        canonical_cohort="finance-leaders",
                        suffix="finance-leaders",
                    ),
                },
            ],
            "ignored_fields": [],
            "privacy_requirements": {
                "permission_confirmed": True,
                "aggregate_only": True,
                "minimum_cell_size": 10,
                "prohibited_routes": ["person_level", "direct_identifier"],
                "resolved_clarifications": [],
            },
            "approval": {
                "status": "approved",
                "approved_by": "data-owner",
                "approved_at": "2026-07-24T13:00:00Z",
                "mapping_sha256": None,
            },
        }
        mapping["field_routes"] = self._complete_field_routes(
            mapping,
            {
                "frame-source": "structural_frame",
                "evidence-source": "overlay_evidence",
                "outcome-source": "outcome_feedback",
            },
        )
        mapping["approval"]["mapping_sha256"] = mapping_sha256(mapping)
        return mapping

    def test_mapping_constants_are_closed_sets(self):
        self.assertEqual("authorized-audience-mapping-v1", AUTHORIZED_MAPPING_VERSION)
        self.assertEqual(
            {
                "structural_frame",
                "overlay_evidence",
                "profile_seed",
                "outcome_feedback",
                "unsupported",
            },
            set(SEMANTIC_ROUTES),
        )
        self.assertEqual(
            {
                "select", "rename", "cast", "flatten", "wide_to_long",
                "pivot", "join", "category_map", "normalize_missing",
                "normalize_suppression", "derive_share", "normalize_weight",
                "aggregate", "filter", "sort",
            },
            set(ALLOWED_OPERATIONS),
        )

    def test_validates_approved_profile_and_input_bound_mapping(self):
        mapping = self._mapping()
        self.assertEqual(mapping, validate_authorized_mapping(mapping, source_profile=self.profile))
        committed = json.loads((FIXTURES / "approved-mapping.json").read_text())
        self.assertEqual(mapping, committed)
        self.assertEqual(committed, validate_authorized_mapping(committed, source_profile=self.profile))

    def test_output_metadata_is_explicit_and_outcome_selectors_are_unique(self):
        mapping = self._mapping()
        del mapping["expected_outputs"][0]["metadata"]["source"]
        mapping["approval"]["mapping_sha256"] = mapping_sha256(mapping)
        with self.assertRaisesRegex(ContractError, "missing fields.*source"):
            validate_authorized_mapping(mapping, source_profile=self.profile)

        mapping = self._mapping()
        mapping["expected_outputs"][4]["metadata"]["record_match"] = deepcopy(
            mapping["expected_outputs"][3]["metadata"]["record_match"]
        )
        mapping["approval"]["mapping_sha256"] = mapping_sha256(mapping)
        with self.assertRaisesRegex(ContractError, "record_match duplicates"):
            validate_authorized_mapping(mapping, source_profile=self.profile)

    def test_explicit_mapping_may_resolve_a_needs_clarification_profile(self):
        profile = deepcopy(self.profile)
        profile["decision"] = {
            "status": "needs_clarification",
            "allowed_next_route": "aggregate_transform",
            "reasons": ["confirm_unit_and_denominator"],
        }
        mapping = self._mapping()
        mapping["source_profile_sha256"] = mapping_sha256(profile)
        mapping["privacy_requirements"]["resolved_clarifications"] = [
            "confirm_unit_and_denominator"
        ]
        mapping["approval"]["mapping_sha256"] = mapping_sha256(mapping)
        self.assertEqual(
            mapping,
            validate_authorized_mapping(mapping, source_profile=profile),
        )

    def test_recomputes_eligibility_and_cannot_bypass_private_aggregation_decision(self):
        profile = deepcopy(self.profile)
        profile["privacy_risk"] = [
            {"code": "email", "field": "segment", "source": "field_name"}
        ]
        profile["decision"] = {
            "status": "ready_for_mapping",
            "allowed_next_route": "aggregate_transform",
            "reasons": [],
        }
        mapping = self._mapping()
        mapping["source_profile_sha256"] = mapping_sha256(profile)
        mapping["approval"]["mapping_sha256"] = mapping_sha256(mapping)
        with self.assertRaisesRegex(ContractError, "privacy risk|private aggregation"):
            validate_authorized_mapping(mapping, source_profile=profile)

    def test_clarification_coverage_is_exact_and_limited_to_permitted_issues(self):
        profile = deepcopy(self.profile)
        profile["decision"] = {
            "status": "needs_clarification",
            "allowed_next_route": "aggregate_transform",
            "reasons": ["confirm_unit"],
        }
        mapping = self._mapping()
        mapping["source_profile_sha256"] = mapping_sha256(profile)
        mapping["approval"]["mapping_sha256"] = mapping_sha256(mapping)
        with self.assertRaisesRegex(ContractError, "clarification"):
            validate_authorized_mapping(mapping, source_profile=profile)
        mapping["privacy_requirements"]["resolved_clarifications"] = ["confirm_unit"]
        mapping["approval"]["mapping_sha256"] = mapping_sha256(mapping)
        self.assertEqual(mapping, validate_authorized_mapping(mapping, source_profile=profile))
        profile["decision"]["reasons"] = ["unsupported_format"]
        mapping["source_profile_sha256"] = mapping_sha256(profile)
        mapping["privacy_requirements"]["resolved_clarifications"] = ["unsupported_format"]
        mapping["approval"]["mapping_sha256"] = mapping_sha256(mapping)
        with self.assertRaisesRegex(ContractError, "permitted clarification"):
            validate_authorized_mapping(mapping, source_profile=profile)

    def test_direct_transform_requires_aggregate_units_and_minimum_cells(self):
        mapping = self._mapping()
        mapping["selections"][0]["unit"] = "person_record"
        mapping["approval"]["mapping_sha256"] = mapping_sha256(mapping)
        with self.assertRaisesRegex(ContractError, "aggregate unit"):
            validate_authorized_mapping(mapping, source_profile=self.profile)

        small_source = self.root / "small"
        small_source.mkdir()
        (small_source / "flat-structural.csv").write_text(
            "segment,respondent_count,share\nsmall_cohort,5,1.0\n",
            encoding="utf-8",
        )
        for name in ("topic-social-evidence.json", "aggregate-outcomes.json"):
            (small_source / name).write_bytes((FIXTURES / name).read_bytes())
        profile = self._profile(
            small_source / "flat-structural.csv",
            small_source / "topic-social-evidence.json",
            small_source / "aggregate-outcomes.json",
        )
        mapping = self._mapping()
        mapping["source_profile_sha256"] = mapping_sha256(profile)
        mapping["input_hashes"] = {
            item["display_name"]: item["sha256"] for item in profile["inputs"]
        }
        for selection in mapping["selections"]:
            selection["file_sha256"] = mapping["input_hashes"][selection["file"]]
        mapping["expected_outputs"][0]["metadata"]["cell_metadata"] = {
            "small_cohort": {
                "cell_id": "small-cohort",
                "uncertainty": {
                    "lower_field": "respondent_count",
                    "upper_field": "respondent_count",
                    "method": "exact approved aggregate count",
                },
                "suppressed": False,
                "status": "observed",
                "relationship": "marginal",
                "source_location": "approved-cohort-export#small-cohort",
            }
        }
        mapping["approval"]["mapping_sha256"] = mapping_sha256(mapping)
        output = self.root / "small-output"
        with self.assertRaisesRegex(ContractError, "minimum cell size"):
            transform_authorized_bundle(
                source_profile=profile,
                mapping=mapping,
                input_root=small_source,
                output_dir=output,
                transformer_version="1.0.0",
            )
        self.assertFalse(output.exists())

    def test_every_profiled_table_requires_selected_or_ignored_field_coverage(self):
        extra = self.source / "unused-table.json"
        extra.write_text('{"records":[{"unused_dimension":"x","unused_metric":2}]}', encoding="utf-8")
        profile = self._profile(
            self.source / "flat-structural.csv",
            self.source / "topic-social-evidence.json",
            self.source / "aggregate-outcomes.json",
            extra,
        )
        mapping = self._mapping()
        mapping["source_profile_sha256"] = mapping_sha256(profile)
        mapping["input_hashes"] = {
            item["display_name"]: item["sha256"] for item in profile["inputs"]
        }
        mapping["approval"]["mapping_sha256"] = mapping_sha256(mapping)
        with self.assertRaisesRegex(ContractError, "profiled table|coverage"):
            validate_authorized_mapping(mapping, source_profile=profile)
        mapping["ignored_fields"] = [
            {
                "file": "unused-table.json",
                "sheet": None,
                "record_path": "records",
                "field": field,
                "reason": "outside approved audience purpose",
            }
            for field in ("unused_dimension", "unused_metric")
        ]
        mapping["approval"]["mapping_sha256"] = mapping_sha256(mapping)
        self.assertEqual(mapping, validate_authorized_mapping(mapping, source_profile=profile))

    def test_semantic_routes_cover_every_dataset_field_and_block_evidence_to_weight(self):
        mapping = self._mapping()
        mapping["field_routes"] = [
            item for item in mapping["field_routes"]
            if not (item["dataset"] == "frame-source" and item["field"] == "share")
        ]
        mapping["approval"]["mapping_sha256"] = mapping_sha256(mapping)
        with self.assertRaisesRegex(ContractError, "every selected and derived field"):
            validate_authorized_mapping(mapping, source_profile=self.profile)

        mapping = self._mapping()
        mapping["operations"].append(
            {
                "operation_id": "aggregate-evidence",
                "op": "aggregate",
                "input": "evidence-source",
                "output": "evidence-weights",
                "group_by": ["cohort_id"],
                "metrics": {
                    "weight": {"field": "evidence_id", "function": "count"}
                },
            }
        )
        mapping["field_routes"].extend(
            [
                {
                    "dataset": "evidence-weights",
                    "field": "cohort_id",
                    "route": "structural_frame",
                },
                {
                    "dataset": "evidence-weights",
                    "field": "weight",
                    "route": "structural_frame",
                },
            ]
        )
        mapping["expected_outputs"][0] = {
            "dataset": "evidence-weights",
            "route": "structural_frame",
            "filename": "frame-observations-0001.json",
            "schema_version": "audience-frame-observation-batch-v1",
        }
        mapping["approval"]["mapping_sha256"] = mapping_sha256(mapping)
        with self.assertRaisesRegex(ContractError, "evidence ancestry|structural|provenance"):
            validate_authorized_mapping(mapping, source_profile=self.profile)

    def test_flatten_provenance_uses_the_selected_top_level_nested_field(self):
        profile = deepcopy(self.profile)
        frame_table = next(
            item
            for item in profile["tables"]
            if item["file"] == "flat-structural.csv"
        )
        frame_table["field_names"] = ["identity", "respondent_count", "share"]
        frame_table["observed_scalar_types"] = {
            "identity": ["object"],
            "respondent_count": ["integer"],
            "share": ["number"],
        }
        frame_table["null_rates"] = {
            "identity": 0.0,
            "respondent_count": 0.0,
            "share": 0.0,
        }
        frame_table["sample_safe_value_classes"] = {
            "identity": ["nested_object"],
            "respondent_count": ["count_like"],
            "share": ["share_like"],
        }
        frame_table["candidate_field_roles"] = {
            "identity": ["unresolved"],
            "respondent_count": ["candidate_count"],
            "share": ["candidate_share_or_rate"],
        }

        mapping = self._mapping()
        mapping["selections"][0]["fields"] = [
            "identity",
            "respondent_count",
            "share",
        ]
        mapping["selections"][0]["aggregate_join_keys"] = []
        mapping["operations"][0] = {
            "operation_id": "flatten-frame",
            "op": "flatten",
            "input": "frame-source",
            "output": "frame",
            "fields": {
                "cohort_id": "identity.segment",
                "respondent_count": "respondent_count",
                "share": "share",
            },
        }
        mapping["field_routes"] = [
            (
                {**item, "field": "identity"}
                if item["dataset"] == "frame-source" and item["field"] == "segment"
                else item
            )
            for item in mapping["field_routes"]
        ]
        mapping["source_profile_sha256"] = mapping_sha256(profile)
        mapping["approval"]["mapping_sha256"] = mapping_sha256(mapping)

        self.assertEqual(
            mapping,
            validate_authorized_mapping(mapping, source_profile=profile),
        )

    def test_mapping_rejects_unknown_keys_hash_mismatches_and_unapproved_state(self):
        cases = [
            ("unknown fields", lambda item: item.update({"expression": "row['x']"})),
            ("source_profile_sha256", lambda item: item.update({"source_profile_sha256": "sha256:" + "0" * 64})),
            ("input_hashes", lambda item: item["input_hashes"].update({"flat-structural.csv": "sha256:" + "0" * 64})),
            ("status", lambda item: item["approval"].update({"status": "draft"})),
        ]
        for expected, mutate in cases:
            with self.subTest(expected=expected):
                mapping = self._mapping()
                mutate(mapping)
                with self.assertRaisesRegex(ContractError, expected):
                    validate_authorized_mapping(mapping, source_profile=self.profile)

        mapping = self._mapping()
        mapping["expected_outputs"][0]["filename"] = "frame-observations-0002.json"
        mapping["approval"]["mapping_sha256"] = mapping_sha256(mapping)
        with self.assertRaisesRegex(ContractError, "contiguous"):
            validate_authorized_mapping(mapping, source_profile=self.profile)

    def test_mapping_uses_null_digest_circular_hash_procedure(self):
        mapping = self._mapping()
        mapping["approval"]["mapping_sha256"] = "sha256:" + "f" * 64
        with self.assertRaisesRegex(ContractError, "mapping_sha256"):
            validate_authorized_mapping(mapping, source_profile=self.profile)
        mapping["approval"]["mapping_sha256"] = None
        expected = mapping_sha256(mapping)
        mapping["approval"]["mapping_sha256"] = expected
        self.assertEqual(expected, validate_authorized_mapping(mapping, source_profile=self.profile)["approval"]["mapping_sha256"])

    def test_mapping_rejects_ambiguous_source_unit_or_denominator(self):
        for key, value in (("unit", "unknown"), ("denominator", "ambiguous")):
            with self.subTest(key=key):
                mapping = self._mapping()
                mapping["selections"][0][key] = value
                mapping["approval"]["mapping_sha256"] = mapping_sha256(mapping)
                with self.assertRaisesRegex(ContractError, key):
                    validate_authorized_mapping(mapping, source_profile=self.profile)

    def test_mapping_rejects_unresolved_or_fuzzy_join_and_unknown_category_policy(self):
        mapping = self._mapping()
        mapping["operations"].insert(
            0,
            {
                "operation_id": "bad-join",
                "op": "join",
                "left": "frame-source",
                "right": "outcome-source",
                "output": "joined",
                "on": ["not_approved"],
                "cardinality": "fuzzy",
            },
        )
        mapping["approval"]["mapping_sha256"] = mapping_sha256(mapping)
        with self.assertRaisesRegex(ContractError, "cardinality|approved aggregate join"):
            validate_authorized_mapping(mapping, source_profile=self.profile)

        mapping = self._mapping()
        mapping["operations"].insert(
            0,
            {
                "operation_id": "bad-categories",
                "op": "category_map",
                "input": "frame-source",
                "output": "mapped",
                "field": "segment",
                "mapping": {"operations_leaders": "operations"},
                "unmapped": "infer",
            },
        )
        mapping["approval"]["mapping_sha256"] = mapping_sha256(mapping)
        with self.assertRaisesRegex(ContractError, "unmapped"):
            validate_authorized_mapping(mapping, source_profile=self.profile)

    def test_mapping_rejects_unsupported_or_executable_operations(self):
        for operation, expected in (
            (
                {"operation_id": "sql", "op": "sql", "query": "select * from data"},
                "op",
            ),
            (
                {
                    "operation_id": "evil",
                    "op": "select",
                    "input": "frame-source",
                    "output": "bad",
                    "fields": ["__import__('os').system('id')"],
                },
                "executable-looking",
            ),
        ):
            with self.subTest(operation=operation):
                mapping = self._mapping()
                mapping["operations"].insert(0, operation)
                mapping["approval"]["mapping_sha256"] = mapping_sha256(mapping)
                with self.assertRaisesRegex(ContractError, expected):
                    validate_authorized_mapping(mapping, source_profile=self.profile)

    def test_mapping_requires_one_route_per_output_field_and_blocks_route_laundering(self):
        mapping = self._mapping()
        mapping["field_routes"].pop()
        mapping["approval"]["mapping_sha256"] = mapping_sha256(mapping)
        with self.assertRaisesRegex(ContractError, "exactly one semantic route|every selected and derived"):
            validate_authorized_mapping(mapping, source_profile=self.profile)

        mapping = self._mapping()
        mapping["field_routes"].append(
            {"dataset": "frame-final", "field": "share", "route": "overlay_evidence"}
        )
        mapping["approval"]["mapping_sha256"] = mapping_sha256(mapping)
        with self.assertRaisesRegex(ContractError, "exactly one semantic route|every selected and derived"):
            validate_authorized_mapping(mapping, source_profile=self.profile)

        mapping = self._mapping()
        next(
            item for item in mapping["field_routes"]
            if item["dataset"] == "frame-source" and item["field"] == "share"
        )["route"] = "overlay_evidence"
        mapping["approval"]["mapping_sha256"] = mapping_sha256(mapping)
        with self.assertRaisesRegex(ContractError, "route laundering|structural|provenance"):
            validate_authorized_mapping(mapping, source_profile=self.profile)

        mapping = self._mapping()
        mapping["operations"].extend(
            [
                {
                    "operation_id": "derive-weight",
                    "op": "normalize_weight",
                    "input": "frame-cast",
                    "output": "weighted",
                    "field": "respondent_count",
                    "output_field": "weight",
                    "group_by": [],
                },
                {
                    "operation_id": "select-weight",
                    "op": "select",
                    "input": "weighted",
                    "output": "weight-only",
                    "fields": ["cohort_id", "respondent_count", "share", "weight"],
                },
                {
                    "operation_id": "hide-weight",
                    "op": "rename",
                    "input": "weight-only",
                    "output": "disguised-weight",
                    "fields": {"weight": "affinity"},
                },
            ]
        )
        mapping["expected_outputs"][0] = {
            "dataset": "disguised-weight",
            "route": "overlay_evidence",
            "filename": "structured-evidence-0002.json",
            "schema_version": "audience-structured-evidence-batch-v1",
        }
        mapping["field_routes"] = self._complete_field_routes(
            mapping,
            {
                "frame-source": "structural_frame",
                "evidence-source": "overlay_evidence",
                "outcome-source": "outcome_feedback",
            },
        )
        for item in mapping["field_routes"]:
            if item["dataset"] == "disguised-weight":
                item["route"] = "overlay_evidence"
        mapping["approval"]["mapping_sha256"] = mapping_sha256(mapping)
        with self.assertRaisesRegex(ContractError, "route laundering|structural|provenance"):
            validate_authorized_mapping(mapping, source_profile=self.profile)

    def test_mapping_validation_happens_before_any_source_read(self):
        mapping = self._mapping()
        mapping["approval"]["status"] = "draft"
        mapping["approval"]["mapping_sha256"] = mapping_sha256(mapping)
        for item in mapping["selections"]:
            item["file"] = "does-not-exist.csv"
        with self.assertRaisesRegex(ContractError, "status"):
            transform_authorized_bundle(
                source_profile=self.profile,
                mapping=mapping,
                input_root=self.source,
                output_dir=self.root / "output",
                transformer_version="1.0.0",
            )

    def test_all_declarative_operations_have_real_deterministic_behavior(self):
        tables = {
            "base": [
                {
                    "segment": "ops",
                    "count": "2",
                    "total": "4",
                    "kind": "A",
                    "missing": "NA",
                    "suppressed": "<10",
                    "x": 1,
                    "y": 2,
                    "unused": 9,
                },
                {
                    "segment": "fin",
                    "count": "2",
                    "total": "4",
                    "kind": "B",
                    "missing": "",
                    "suppressed": 0,
                    "x": 3,
                    "y": 4,
                    "unused": 9,
                },
            ],
            "lookup": [
                {"segment": "ops", "label": "Operations"},
                {"segment": "fin", "label": "Finance"},
            ],
            "nested": [{"identity": {"segment": "ops"}, "value": 2}],
            "long": [
                {"segment": "ops", "metric": "count", "value": 2},
                {"segment": "ops", "metric": "total", "value": 4},
            ],
        }
        operations = [
            {"operation_id": "select", "op": "select", "input": "base", "output": "s1", "fields": ["segment", "count", "total", "kind", "missing", "suppressed", "x", "y"]},
            {"operation_id": "rename", "op": "rename", "input": "s1", "output": "s2", "fields": {"kind": "category"}},
            {"operation_id": "cast", "op": "cast", "input": "s2", "output": "s3", "fields": {"count": "integer", "total": "number"}},
            {"operation_id": "missing", "op": "normalize_missing", "input": "s3", "output": "s4", "fields": ["missing"], "values": ["", "NA"]},
            {"operation_id": "suppression", "op": "normalize_suppression", "input": "s4", "output": "s5", "field": "suppressed", "values": ["<10"], "status_field": "is_suppressed"},
            {"operation_id": "category", "op": "category_map", "input": "s5", "output": "s6", "field": "category", "mapping": {"A": "alpha", "B": "beta"}, "unmapped": "error"},
            {"operation_id": "share", "op": "derive_share", "input": "s6", "output": "s7", "count_field": "count", "denominator_field": "total", "output_field": "share"},
            {"operation_id": "weight", "op": "normalize_weight", "input": "s7", "output": "s8", "field": "count", "output_field": "weight", "group_by": []},
            {"operation_id": "filter", "op": "filter", "input": "s8", "output": "s9", "field": "category", "predicate": "in", "value": ["alpha", "beta"]},
            {"operation_id": "join", "op": "join", "left": "s9", "right": "lookup", "output": "s10", "on": ["segment"], "cardinality": "many_to_one"},
            {"operation_id": "aggregate", "op": "aggregate", "input": "s10", "output": "s11", "group_by": ["category"], "metrics": {"count_sum": {"field": "count", "function": "sum"}}},
            {"operation_id": "sort", "op": "sort", "input": "s11", "output": "s12", "fields": ["category"]},
            {"operation_id": "flatten", "op": "flatten", "input": "nested", "output": "flat", "fields": {"segment": "identity.segment", "value": "value"}},
            {"operation_id": "wide", "op": "wide_to_long", "input": "base", "output": "wide-long", "id_fields": ["segment"], "value_fields": ["x", "y"], "name_field": "metric", "value_field": "value"},
            {"operation_id": "pivot", "op": "pivot", "input": "long", "output": "pivoted", "index_fields": ["segment"], "column_field": "metric", "value_field": "value", "columns": ["count", "total"]},
        ]
        result, report = apply_authorized_operations(tables, operations)
        self.assertEqual(
            [{"category": "alpha", "count_sum": 2}, {"category": "beta", "count_sum": 2}],
            result["s12"],
        )
        self.assertEqual([{"segment": "ops", "value": 2}], result["flat"])
        self.assertEqual(
            [
                {"segment": "fin", "metric": "x", "value": 3},
                {"segment": "fin", "metric": "y", "value": 4},
                {"segment": "ops", "metric": "x", "value": 1},
                {"segment": "ops", "metric": "y", "value": 2},
            ],
            result["wide-long"],
        )
        self.assertEqual([{"segment": "ops", "count": 2, "total": 4}], result["pivoted"])
        self.assertIsNone(result["s5"][0]["suppressed"])
        self.assertTrue(result["s5"][0]["is_suppressed"])
        self.assertEqual(0, result["s5"][1]["suppressed"])
        self.assertFalse(result["s5"][1]["is_suppressed"])
        self.assertEqual(15, len(report))
        self.assertEqual(["unused"], report[0]["details"]["dropped_fields"])

    def test_filter_reports_loss_and_duplicate_join_keys_fail(self):
        tables = {
            "left": [{"id": "a"}, {"id": "b"}],
            "right": [{"id": "a", "value": 1}, {"id": "a", "value": 2}],
        }
        with self.assertRaisesRegex(ContractError, "duplicate join key"):
            apply_authorized_operations(
                tables,
                [{"operation_id": "j", "op": "join", "left": "left", "right": "right", "output": "joined", "on": ["id"], "cardinality": "many_to_one"}],
            )
        result, report = apply_authorized_operations(
            {"rows": [{"id": "a"}, {"id": "b"}]},
            [{"operation_id": "f", "op": "filter", "input": "rows", "output": "kept", "field": "id", "predicate": "equals", "value": "a"}],
        )
        self.assertEqual([{"id": "a"}], result["kept"])
        self.assertEqual(1, report[0]["filtered_rows"])
        self.assertEqual("row_filter", report[0]["loss_consequence"])

    def test_join_aggregation_and_category_merge_report_all_loss_consequences(self):
        tables = {
            "left": [{"id": "a"}, {"id": "b"}],
            "right": [
                {"id": "a", "label": "A"},
                {"id": "b", "label": "B"},
                {"id": "unused", "label": "Unused"},
            ],
            "categories": [
                {"category": "A", "value": 1},
                {"category": "B", "value": 2},
            ],
        }
        result, audit = apply_authorized_operations(
            tables,
            [
                {"operation_id": "join", "op": "join", "left": "left", "right": "right", "output": "joined", "on": ["id"], "cardinality": "many_to_one"},
                {"operation_id": "merge", "op": "category_map", "input": "categories", "output": "merged", "field": "category", "mapping": {"A": "combined", "B": "combined"}, "unmapped": "error"},
                {"operation_id": "aggregate", "op": "aggregate", "input": "merged", "output": "totals", "group_by": ["category"], "metrics": {"total": {"field": "value", "function": "sum"}}},
            ],
        )
        self.assertEqual(1, audit[0]["details"]["unused_right_rows"])
        self.assertIn("unmatched_join_rows", audit[0]["loss_consequences"])
        self.assertIn("category_merge", audit[1]["loss_consequences"])
        self.assertIn("aggregation_granularity", audit[2]["loss_consequences"])
        self.assertEqual([{"category": "combined", "total": 3}], result["totals"])

    def test_transform_writes_valid_hash_bound_outputs_without_clobbering(self):
        mapping = self._mapping()
        output = self.root / "handoff"
        handoff = transform_authorized_bundle(
            source_profile=self.profile,
            mapping=mapping,
            input_root=self.source,
            output_dir=output,
            transformer_version="1.0.0",
        )
        self.assertEqual(AUTHORIZED_HANDOFF_VERSION, handoff["schema_version"])
        self.assertEqual("complete", handoff["status"])
        self.assertEqual(
            {"path": "approved-source-profile.json", "sha256": mapping_sha256(self.profile)},
            handoff["source_profile"],
        )
        self.assertEqual(
            {
                "path": "approved-mapping.json",
                "sha256": sha256_bytes(canonical_json_bytes(self._mapping())),
            },
            handoff["mapping"],
        )
        self.assertEqual(handoff, validate_authorized_handoff(handoff, output_root=output))
        report = json.loads((output / "transformation-report.json").read_text())
        self.assertEqual(TRANSFORMATION_REPORT_VERSION, report["schema_version"])
        self.assertEqual("complete", report["status"])
        self.assertEqual("aggregate_cohort", report["outputs"][0]["unit"])
        self.assertEqual("all_respondents", report["outputs"][0]["denominator"])
        self.assertEqual(report, validate_transformation_report(report))
        for item in handoff["outputs"]:
            self.assertEqual(item["sha256"], sha256_file(output / item["path"]))
        before = {path.name: path.read_bytes() for path in output.iterdir()}
        with self.assertRaisesRegex(ContractError, "already exists"):
            transform_authorized_bundle(
                source_profile=self.profile,
                mapping=mapping,
                input_root=self.source,
                output_dir=output,
                transformer_version="1.0.0",
            )
        self.assertEqual(before, {path.name: path.read_bytes() for path in output.iterdir()})

    def test_emitted_documents_pass_authoritative_downstream_validators(self):
        output = self.root / "canonical-integration"
        transform_authorized_bundle(
            source_profile=self.profile,
            mapping=self._mapping(),
            input_root=self.source,
            output_dir=output,
            transformer_version="1.0.0",
        )

        frame = json.loads((output / "frame-observations-0001.json").read_text())
        self.assertEqual(frame, validate_observation_batch(frame))

        outcomes = [
            json.loads(path.read_text())
            for path in sorted(output.glob("outcome-feedback-*.json"))
        ]
        self.assertEqual(2, len(outcomes))
        for outcome in outcomes:
            self.assertEqual(
                outcome,
                validate_outcome_feedback(outcome)["canonical_copy"],
            )

        structured = json.loads(
            (output / "structured-evidence-0001.json").read_text()
        )
        ledger = build_evidence_ledger(
            "authorized-integration",
            [structured],
            created_at="2026-07-24T13:00:00Z",
        )
        self.assertEqual(2, ledger["summary"]["accepted_items"])

        social = json.loads(
            (output / "social-observations-0001.json").read_text()
        )
        social_ledger = build_evidence_ledger(
            "authorized-social-integration",
            [social],
            created_at="2026-07-24T13:00:00Z",
        )
        self.assertEqual(2, social_ledger["summary"]["accepted_items"])

    def test_publish_failure_removes_staging_directory_and_never_exposes_partial_output(self):
        output = self.root / "failed-publication"
        real_write = write_new_bytes
        writes = 0

        def fail_during_publication(path, data, label):
            nonlocal writes
            writes += 1
            if writes == 3:
                raise OSError("simulated publication failure")
            return real_write(path, data, label)

        with mock.patch(
            "audience_data_lab.authorized_transform.write_new_bytes",
            side_effect=fail_during_publication,
        ):
            with self.assertRaisesRegex(OSError, "simulated publication failure"):
                transform_authorized_bundle(
                    source_profile=self.profile,
                    mapping=self._mapping(),
                    input_root=self.source,
                    output_dir=output,
                    transformer_version="1.0.0",
                )

        self.assertFalse(output.exists())
        self.assertEqual(
            [],
            list(output.parent.glob(f".{output.name}.publishing-*")),
        )

    def test_source_replacement_after_open_cannot_change_consumed_snapshot(self):
        source = self.root / "snapshot.csv"
        replacement = self.root / "replacement.csv"
        original_bytes = b"cohort_id,count\nops,20\n"
        replacement_bytes = b"cohort_id,count\nfinance,999\n"
        source.write_bytes(original_bytes)
        replacement.write_bytes(replacement_bytes)
        expected_digest = sha256_bytes(original_bytes)
        original_handle = source.open("rb")

        class ReplacePathBeforeRead:
            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, traceback):
                original_handle.close()

            def fileno(self):
                return original_handle.fileno()

            def read(self):
                replacement.replace(source)
                return original_handle.read()

        with mock.patch.object(
            Path,
            "open",
            return_value=ReplacePathBeforeRead(),
        ):
            consumed, digest = _snapshot_input(source, expected_digest)

        self.assertEqual(original_bytes, consumed)
        self.assertEqual(expected_digest, digest)
        self.assertEqual(replacement_bytes, source.read_bytes())

    def test_destination_created_after_preflight_is_never_replaced_at_publication(self):
        output = self.root / "racing-destination"
        real_exists = Path.exists
        output_exists_calls = 0
        raced_inode: int | None = None

        def race_on_final_preflight(path):
            nonlocal output_exists_calls, raced_inode
            if path == output:
                output_exists_calls += 1
                if output_exists_calls == 2:
                    output.mkdir()
                    raced_inode = output.stat().st_ino
                    return False
            return real_exists(path)

        with mock.patch.object(
            Path,
            "exists",
            autospec=True,
            side_effect=race_on_final_preflight,
        ):
            with self.assertRaisesRegex(ContractError, "already exists"):
                transform_authorized_bundle(
                    source_profile=self.profile,
                    mapping=self._mapping(),
                    input_root=self.source,
                    output_dir=output,
                    transformer_version="1.0.0",
                )

        self.assertEqual(raced_inode, output.stat().st_ino)
        self.assertEqual([], list(output.iterdir()))
        self.assertEqual(
            [],
            list(output.parent.glob(f".{output.name}.publishing-*")),
        )

    def test_route_schema_registry_and_document_semantics_validate_before_publish(self):
        mapping = self._mapping()
        mapping["expected_outputs"][0]["schema_version"] = "anything-v1"
        mapping["approval"]["mapping_sha256"] = mapping_sha256(mapping)
        with self.assertRaisesRegex(ContractError, "route/schema"):
            validate_authorized_mapping(mapping, source_profile=self.profile)

        mapping = self._mapping()
        mapping["operations"].append(
            {
                "operation_id": "rename-cohort-id",
                "op": "rename",
                "input": "frame-final",
                "output": "invalid-frame",
                "fields": {"cohort_id": "group_id"},
            }
        )
        mapping["field_routes"].extend(
            [
                {"dataset": "invalid-frame", "field": "group_id", "route": "structural_frame"},
                {"dataset": "invalid-frame", "field": "respondent_count", "route": "structural_frame"},
                {"dataset": "invalid-frame", "field": "share", "route": "structural_frame"},
            ]
        )
        mapping["expected_outputs"][0]["dataset"] = "invalid-frame"
        mapping["approval"]["mapping_sha256"] = mapping_sha256(mapping)
        output = self.root / "invalid-document"
        with self.assertRaisesRegex(ContractError, "cohort_id|canonical"):
            transform_authorized_bundle(
                source_profile=self.profile,
                mapping=mapping,
                input_root=self.source,
                output_dir=output,
                transformer_version="1.0.0",
            )
        self.assertFalse(output.exists())

    def test_cli_writes_handoff_and_preserves_existing_output(self):
        profile_path = self.root / "approved-profile.json"
        mapping_path = self.root / "approved-mapping.json"
        profile_path.write_bytes(canonical_json_bytes(self.profile))
        mapping_path.write_bytes(canonical_json_bytes(self._mapping()))
        output = self.root / "cli-output"
        command = [
            sys.executable,
            str(SCRIPTS / "transform-authorized-audience.py"),
            "--profile",
            str(profile_path),
            "--mapping",
            str(mapping_path),
            "--input-root",
            str(self.source),
            "--output-dir",
            str(output),
            "--transformer-version",
            "1.0.0",
        ]
        completed = subprocess.run(command, text=True, capture_output=True, check=False)
        self.assertEqual(0, completed.returncode, completed.stderr)
        self.assertIn("authorized-audience-handoff.json complete", completed.stdout)
        before = {path.name: path.read_bytes() for path in output.iterdir()}
        repeated = subprocess.run(command, text=True, capture_output=True, check=False)
        self.assertEqual(2, repeated.returncode)
        self.assertIn("already exists", repeated.stderr)
        self.assertEqual(before, {path.name: path.read_bytes() for path in output.iterdir()})

    def test_handoff_validation_rejects_path_escape_and_hash_mismatch(self):
        output = self.root / "handoff"
        handoff = transform_authorized_bundle(
            source_profile=self.profile,
            mapping=self._mapping(),
            input_root=self.source,
            output_dir=output,
            transformer_version="1.0.0",
        )
        escaped = deepcopy(handoff)
        escaped["outputs"][0]["path"] = "../outside.json"
        with self.assertRaisesRegex(ContractError, "relative output path"):
            validate_authorized_handoff(escaped, output_root=output)
        mismatched = deepcopy(handoff)
        mismatched["outputs"][0]["sha256"] = "sha256:" + "0" * 64
        with self.assertRaisesRegex(ContractError, "hash"):
            validate_authorized_handoff(mismatched, output_root=output)
        status_mismatch = deepcopy(handoff)
        status_mismatch["status"] = "complete_with_loss"
        with self.assertRaisesRegex(ContractError, "transformation report"):
            validate_authorized_handoff(status_mismatch, output_root=output)
        omitted = deepcopy(handoff)
        omitted["outputs"].pop()
        with self.assertRaisesRegex(ContractError, "transformation report"):
            validate_authorized_handoff(omitted, output_root=output)
        frame_path = output / "frame-observations-0001.json"
        frame = json.loads(frame_path.read_text())
        frame["unknown"] = True
        frame_bytes = canonical_json_bytes(frame)
        frame_path.write_bytes(frame_bytes)
        frame_hash = sha256_bytes(frame_bytes)
        report_path = output / "transformation-report.json"
        report = json.loads(report_path.read_text())
        report["outputs"][0]["sha256"] = frame_hash
        report_bytes = canonical_json_bytes(report)
        report_path.write_bytes(report_bytes)
        semantic_mismatch = deepcopy(handoff)
        semantic_mismatch["outputs"][0]["sha256"] = frame_hash
        semantic_mismatch["transformation_report"]["sha256"] = sha256_bytes(
            report_bytes
        )
        with self.assertRaisesRegex(ContractError, "authoritative validation"):
            validate_authorized_handoff(semantic_mismatch, output_root=output)
        frame.pop("unknown")
        frame_path.write_bytes(canonical_json_bytes(frame))
        (output / "approved-source-profile.json").write_text("{}\n", encoding="utf-8")
        with self.assertRaisesRegex(ContractError, "source profile.*hash|hash mismatch"):
            validate_authorized_handoff(handoff, output_root=output)

    def test_report_nested_schemas_and_summary_coherence_are_strict(self):
        output = self.root / "strict-report"
        transform_authorized_bundle(
            source_profile=self.profile,
            mapping=self._mapping(),
            input_root=self.source,
            output_dir=output,
            transformer_version="1.0.0",
        )
        report = json.loads((output / "transformation-report.json").read_text())
        report["operation_log"][0]["details"]["unknown"] = True
        with self.assertRaisesRegex(ContractError, "unknown fields"):
            validate_transformation_report(report)
        report = json.loads((output / "transformation-report.json").read_text())
        report["route_summary"][0]["row_count"] += 1
        with self.assertRaisesRegex(ContractError, "route summary|coherence"):
            validate_transformation_report(report)

    def test_report_rejects_malformed_operation_details_and_unknown_coverage(self):
        output = self.root / "strict-details"
        transform_authorized_bundle(
            source_profile=self.profile,
            mapping=self._mapping(),
            input_root=self.source,
            output_dir=output,
            transformer_version="1.0.0",
        )
        baseline = json.loads((output / "transformation-report.json").read_text())
        malformed_details = [
            (
                "derive_share",
                {
                    "derived_field": 7,
                    "count_field": "respondent_count",
                    "denominator_field": "denominator",
                },
            ),
            (
                "aggregate",
                {
                    "aggregate_group_by": ["cohort_id"],
                    "aggregate_metrics": {
                        "total": {"field": 4, "function": "eval"}
                    },
                },
            ),
            (
                "filter",
                {
                    "predicate": "execute",
                    "field": "cohort_id",
                    "filtered_values": 0,
                },
            ),
            (
                "category_map",
                {
                    "category_merges": {},
                    "unmapped_values": 0,
                    "unmapped_policy": "guess",
                },
            ),
        ]
        for op, details in malformed_details:
            with self.subTest(op=op):
                report = deepcopy(baseline)
                report["operation_log"][0]["op"] = op
                report["operation_log"][0]["details"] = details
                with self.assertRaises(ContractError):
                    validate_transformation_report(report)

        report = deepcopy(baseline)
        report["loss_summary"]["coverage"] = "trust_me"
        with self.assertRaisesRegex(ContractError, "coverage"):
            validate_transformation_report(report)

    def test_handoff_rejects_operation_details_that_disagree_with_approved_mapping(self):
        output = self.root / "mapping-detail-coherence"
        handoff = transform_authorized_bundle(
            source_profile=self.profile,
            mapping=self._mapping(),
            input_root=self.source,
            output_dir=output,
            transformer_version="1.0.0",
        )
        report_path = output / "transformation-report.json"
        report = json.loads(report_path.read_text())
        report["operation_log"][0]["details"]["renamed_fields"] = {
            "segment": "different_cohort_id"
        }
        report_bytes = canonical_json_bytes(report)
        report_path.write_bytes(report_bytes)
        handoff["transformation_report"]["sha256"] = sha256_bytes(report_bytes)

        with self.assertRaisesRegex(ContractError, "mapping|details|cohere"):
            validate_authorized_handoff(handoff, output_root=output)

    def test_flat_nested_linked_and_generated_xlsx_converge_to_identical_documents(self):
        from openpyxl import Workbook

        shape_sources: list[tuple[str, list[Path]]] = []
        flat_dir = self.root / "flat"
        flat_dir.mkdir()
        flat = flat_dir / "flat-structural.csv"
        flat.write_bytes((SHAPES / "flat-structural.csv").read_bytes())
        shape_sources.append(("flat", [flat]))
        nested_dir = self.root / "nested"
        nested_dir.mkdir()
        nested = nested_dir / "nested-export.json"
        nested.write_bytes((SHAPES / "nested-export.json").read_bytes())
        shape_sources.append(("nested", [nested]))
        linked_dir = self.root / "linked"
        linked_dir.mkdir()
        linked_cohorts = linked_dir / "linked-cohorts.csv"
        linked_distributions = linked_dir / "linked-distributions.csv"
        linked_cohorts.write_bytes((SHAPES / "linked-cohorts.csv").read_bytes())
        linked_distributions.write_bytes((SHAPES / "linked-distributions.csv").read_bytes())
        shape_sources.append(("linked", [linked_cohorts, linked_distributions]))
        xlsx_dir = self.root / "xlsx"
        xlsx_dir.mkdir()
        xlsx = xlsx_dir / "cohort.xlsx"
        workbook = Workbook()
        sheet = workbook.active
        sheet.title = "Cohort Summary"
        sheet.append(["metric", "operations_leaders", "finance_leaders"])
        sheet.append(["respondent_count", 420, 680])
        sheet.append(["share", 0.38, 0.62])
        workbook.save(xlsx)
        shape_sources.append(("xlsx", [xlsx]))

        canonical_bytes: list[bytes] = []
        normalized_reports: list[dict[str, object]] = []
        for shape, primary_paths in shape_sources:
            bundle = primary_paths[0].parent
            for fixture_name in ("topic-social-evidence.json", "aggregate-outcomes.json"):
                (bundle / fixture_name).write_bytes((FIXTURES / fixture_name).read_bytes())
            all_paths = primary_paths + [
                bundle / "topic-social-evidence.json",
                bundle / "aggregate-outcomes.json",
            ]
            profile = self._profile(*all_paths)
            mapping = self._mapping_for_shape(shape, profile)
            output = self.root / f"output-{shape}"
            transform_authorized_bundle(
                source_profile=profile,
                mapping=mapping,
                input_root=bundle,
                output_dir=output,
                transformer_version="1.0.0",
            )
            canonical_bytes.append(
                b"".join(
                    (output / name).read_bytes()
                    for name in (
                        "frame-observations-0001.json",
                        "structured-evidence-0001.json",
                        "social-observations-0001.json",
                        "outcome-feedback-0001.json",
                        "outcome-feedback-0002.json",
                    )
                )
            )
            report = json.loads((output / "transformation-report.json").read_text())
            allowed_shape_provenance = {
                "source_profile",
                "mapping",
                "input_hashes",
                "source_reads",
                "operation_log",
                "field_changes",
            }
            normalized_reports.append(
                {
                    key: value
                    for key, value in report.items()
                    if key not in allowed_shape_provenance
                }
            )
        self.assertEqual([canonical_bytes[0]] * 4, canonical_bytes)
        self.assertEqual([normalized_reports[0]] * 4, normalized_reports)

    def _mapping_for_shape(self, shape: str, profile: dict[str, object]) -> dict[str, object]:
        mapping = self._mapping()
        frame_output = mapping["expected_outputs"][0]
        mapping["operations"] = [
            item for item in mapping["operations"]
            if item["operation_id"] not in {"rename-frame", "cast-frame", "sort-frame"}
        ]
        if shape == "flat":
            selection = self._shape_selection(profile, "frame-source", "flat-structural.csv", "$", None, ["segment", "respondent_count", "share"])
            operations = [
                {"operation_id": "rename-frame", "op": "rename", "input": "frame-source", "output": "frame", "fields": {"segment": "cohort_id"}},
            ]
        elif shape == "nested":
            selection = self._shape_selection(profile, "frame-source", "nested-export.json", "export.cohorts", None, ["cohort_label", "respondent_count", "share"])
            operations = [
                {"operation_id": "rename-frame", "op": "rename", "input": "frame-source", "output": "frame", "fields": {"cohort_label": "cohort_id"}},
            ]
        elif shape == "linked":
            selection = self._shape_selection(profile, "cohorts", "linked-cohorts.csv", "$", None, ["cohort_id", "respondent_count"])
            distribution = self._shape_selection(profile, "distributions", "linked-distributions.csv", "$", None, ["cohort_id", "metric", "value"])
            mapping["selections"].insert(1, distribution)
            operations = [
                {"operation_id": "pivot-share", "op": "pivot", "input": "distributions", "output": "shares", "index_fields": ["cohort_id"], "column_field": "metric", "value_field": "value", "columns": ["share"]},
                {"operation_id": "join-frame", "op": "join", "left": "cohorts", "right": "shares", "output": "frame", "on": ["cohort_id"], "cardinality": "one_to_one"},
            ]
        else:
            selection = self._shape_selection(
                profile,
                "frame-source",
                "cohort.xlsx",
                "$",
                "Cohort Summary",
                ["metric", "operations_leaders", "finance_leaders"],
            )
            operations = [
                {
                    "operation_id": "wide-frame",
                    "op": "wide_to_long",
                    "input": "frame-source",
                    "output": "frame-long",
                    "id_fields": ["metric"],
                    "value_fields": ["operations_leaders", "finance_leaders"],
                    "name_field": "cohort_id",
                    "value_field": "value",
                },
                {
                    "operation_id": "pivot-frame",
                    "op": "pivot",
                    "input": "frame-long",
                    "output": "frame",
                    "index_fields": ["cohort_id"],
                    "column_field": "metric",
                    "value_field": "value",
                    "columns": ["respondent_count", "share"],
                },
            ]
        mapping["selections"][0] = selection
        operations.append(
            {
                "operation_id": "cast-frame",
                "op": "cast",
                "input": "frame",
                "output": "frame-cast",
                "fields": {"respondent_count": "integer", "share": "number"},
            }
        )
        operations.append({"operation_id": "sort-frame", "op": "sort", "input": "frame-cast", "output": "frame-final", "fields": ["cohort_id"]})
        mapping["operations"] = operations + mapping["operations"]
        frame_output["dataset"] = "frame-final"
        mapping["input_hashes"] = {
            item["display_name"]: item["sha256"] for item in profile["inputs"]
        }
        for selection in mapping["selections"]:
            selection["file_sha256"] = mapping["input_hashes"][selection["file"]]
        source_routes = {
            selection["selection_id"]: (
                "overlay_evidence"
                if selection["selection_id"] == "evidence-source"
                else "outcome_feedback"
                if selection["selection_id"] == "outcome-source"
                else "structural_frame"
            )
            for selection in mapping["selections"]
        }
        mapping["field_routes"] = self._complete_field_routes(mapping, source_routes)
        mapping["source_profile_sha256"] = mapping_sha256(profile)
        mapping["approval"]["mapping_sha256"] = mapping_sha256(mapping)
        return mapping

    def _shape_selection(
        self,
        profile: dict[str, object],
        selection_id: str,
        file_name: str,
        record_path: str,
        sheet: str | None,
        fields: list[str],
    ) -> dict[str, object]:
        digest = next(item["sha256"] for item in profile["inputs"] if item["display_name"] == file_name)
        return {
            "selection_id": selection_id,
            "file": file_name,
            "file_sha256": digest,
            "sheet": sheet,
            "record_path": record_path,
            "fields": fields,
            "unit": "aggregate_cohort",
            "denominator": "all_respondents",
            "aggregate_join_keys": ["cohort_id"] if "cohort_id" in fields else [],
        }


if __name__ == "__main__":
    unittest.main()
