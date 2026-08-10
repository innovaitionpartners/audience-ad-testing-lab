"""Release C1 end-to-end proofs and protected-behavior compatibility matrix."""

from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "conformance" / "fixtures" / "tier4"
sys.path.insert(0, str(ROOT / "skills" / "audience-panel-builder" / "scripts"))

from audience_panel_builder.common import ContractError, sha256_json  # noqa: E402
from audience_panel_builder.population.validation.contracts import (  # noqa: E402
    validate_validation_observation,
)
from audience_panel_builder.population.validation.library import (  # noqa: E402
    LibraryNotFoundError,
)
from audience_panel_builder.population.validation.metrics import (  # noqa: E402
    normalize_observation,
)
from audience_panel_builder.population.validation.synthetic import (  # noqa: E402
    FrozenOrdering,
    build_synthetic_outcome_comparison,
)
from conformance.test_tier4_held_out_evaluation import (  # noqa: E402
    build_claim_family, comparison, evaluate_held_out_ordering,
    issue_tier4_claim, reseal_comparison_evidence,
    seal_preregistration,
    sealed_registration,
)
from conformance.test_tier4_validation_library import (  # noqa: E402
    append_claim_lifecycle_event, current_claim,
    register_validation_package, show_claim,
)
from conformance.test_tier4_validation_package import (  # noqa: E402
    build_validation_package, validate_validation_package,
)
from conformance.test_tier4_validation_contracts import (  # noqa: E402
    digest,
    observation_fixture,
    preregistration_fixture,
)
from conformance.test_tier4_validation_metrics import (  # noqa: E402
    _reseal_observation,
    observation_fixture_for,
)


EXPECTED_V2_GENERATOR_SHA256 = (
    "62b38b8a7f7265c89682627f8f30a3ccf9ab0fc8142227389de2aaeca5609f5e"
)
EXPECTED_PROTECTED_PRE_C1_SHA256 = {
    "persona_prompt": "8cfc2806d9f6bfdd4a3193eda33c4c3adbd6a4bc69eb0ff6f9187f0b2aab25ff",
    "enriched_prompt_order": "418da32e0777c16f8ac8b8fbda8bd4b2b06d1559f8447b2ca4d65d019e1b700a",
    "progressive_calls": "f3e4f938302913e9e06de84948dc1bb37f83e6d36b2b9f29f4698ffd578a1298",
    "enriched_progressive_calls": "e4ba9db0b484fc42b73e512e9de9ef44db9ba1dbbc5dba7da895066aa646e6ba",
    "validated_response": "cf84bd3d0cd4ef8826e48af6526bc00ec7c378a0842f423135389a043f743ef3",
    "retry_decisions": "f3ee2c7ce53f9de798c537bd6eae43c545bc52f1ce7f2ec9cdbf9fe83030976c",
    "complete_exposure_scores": "0d09f97de44254c4419f2b24fcd11e997333b7a11cfc2c8de54cd2d1afbbd2c2",
    "maxdiff_results": "ab17525b481fd6c7dccb03a9095cde89cc4cfe56088934f033171409094413d1",
    "pairwise_results": "dc3d8c45650bf0ee4b80ec2754797b8bc387eca1830d2c34e0a8f480b063f8bc",
    "finalist_summaries": "6bff1388fb0e286451cb29d550c856ed33bd83afb65d574d372be22dccc89bd1",
    "verbatim_extraction": "b03f32502d699e5ffea9efffd82184f5bbc72c5f2f358e81def560c99cbf6bf1",
}
EXPECTED_MAXIMUM_CAPACITY = {
    "screening_planned": 225,
    "boundary_reserved": 16,
    "finalist_reserved": 8,
    "required_total": 249,
    "ceiling": 249,
    "ceiling_satisfied": True,
    "boundary_jobs_per_wave": 8,
    "boundary_waves_max": 2,
    "shortfall": 0,
}
EXPECTED_MAXIMUM_CAPACITY_SHA256 = {
    "capacity": "8b6ea9fb9c35df0dbe9a7aa5d68ff608e5c660de287dd3a28b312f6b78e2b88c",
    "screening_slot_ids": "b03291bd162ac5efc7a7a5760c011b22f4c7271d4560feac41bf3fe9e1bfbcef",
    "boundary_slot_ids": "2de384d76a3acb9b31923b57aab406386e11ca0917a13d5987a7dcb64466777c",
    "finalist_slot_ids": "3943073c977770359f632ce609be97a64f8af352d569d75243d3be6acd51b938",
}


def _load(relative: str) -> object:
    return json.loads((FIXTURES / relative).read_text(encoding="utf-8"))


def _family(registration: dict[str, object], comparisons: list[dict[str, object]]) -> dict[str, object]:
    return build_claim_family(
        registrations=[registration],
        comparisons_by_registration={registration["registration_id"]: comparisons},
        built_at="2026-09-01T00:00:00Z",
    )


def _evaluate(
    registration: dict[str, object],
    comparisons: list[dict[str, object]],
    family: dict[str, object] | None = None,
) -> dict[str, object]:
    return evaluate_held_out_ordering(
        registration=registration,
        comparisons=comparisons,
        claim_family=family or _family(registration, comparisons),
        evaluated_at="2026-09-01T00:00:00Z",
    )


def _reseal_comparison(document: dict[str, object]) -> dict[str, object]:
    return reseal_comparison_evidence(document)


def _evaluation_projection(document: dict[str, object]) -> dict[str, object]:
    return {
        "schema_version": document["schema_version"],
        "evaluation_id": document["evaluation_id"],
        "evaluation_sha256": document["evaluation_sha256"],
        "decision": document["decision"],
        "block_count": len(document["block_inventory"]),
        "coverage": document["coverage"],
        "missingness": document["missingness"],
        "sample_sufficiency": {
            "status": document["sample_sufficiency"]["status"],
            "minimum_achieved_ratio": document["sample_sufficiency"][
                "minimum_achieved_ratio"
            ],
        },
        "power": document["power"],
        "independence": document["independence"],
        "overall_diagnostics": document["overall_diagnostics"],
        "segment_diagnostics": document["segment_diagnostics"],
        "limitations": document["limitations"],
    }


def _claim_projection(document: dict[str, object]) -> dict[str, object]:
    return {
        key: document[key]
        for key in (
            "schema_version",
            "claim_id",
            "claim_sha256",
            "status",
            "claim_text",
            "required_disclaimer",
            "expires_at",
            "refresh_triggers",
        )
    }


def _gate_observations(
    registration: dict[str, object],
    *,
    holdout_status: str,
    design: str,
) -> list[dict[str, object]]:
    creative_hashes = {
        item["creative_id"]: item["creative_sha256"]
        for item in registration["synthetic_surface"]["eligible_creatives"]
    }
    block = registration["validation_blocks"][0]
    rows: list[dict[str, object]] = []
    for index, (creative_id, creative_sha256) in enumerate(
        sorted(creative_hashes.items()),
    ):
        row = observation_fixture(registration)
        row["observation_id"] = f"gate-observation-{index}"
        row["block_id"] = block["block_id"]
        row["arm_id"] = block["planned_arm_ids"][index]
        row["creative_binding"] = {
            "creative_id": creative_id,
            "creative_sha256": creative_sha256,
        }
        row["shared_outcome_evidence_binding"]["study_id"] = block["study_id"]
        row["assignment"]["design"] = design
        row["holdout_status"] = holdout_status
        _reseal_observation(row)
        rows.append(row)
    return rows


def _assert_downstream_holdout_gate(
    case: unittest.TestCase,
    registration: dict[str, object],
    observations: list[dict[str, object]],
) -> None:
    creative_hashes = tuple(
        sorted(
            (
                item["creative_id"],
                item["creative_sha256"],
            )
            for item in registration["synthetic_surface"]["eligible_creatives"]
        ),
    )
    ordering = FrozenOrdering(
        surface=registration["synthetic_surface"]["surface"],
        run_id=registration["synthetic_surface"]["run_id"],
        result_sha256=registration["synthetic_surface"]["result_sha256"],
        ordered_groups=tuple((creative_id,) for creative_id, _ in creative_hashes),
        creative_hashes=creative_hashes,
    )
    with patch(
        "audience_panel_builder.population.validation.synthetic.load_frozen_ordering",
        return_value=ordering,
    ), case.assertRaisesRegex(ContractError, "not an eligible held-out arm"):
        build_synthetic_outcome_comparison(
            registration=registration,
            result={},
            evidence_root=Path("."),
            snapshot_root=Path("."),
            observations=observations,
        )


class Tier4ValidationGoldenPathTests(unittest.TestCase):
    maxDiff = None

    def test_positive_twelve_block_randomized_path_matches_golden_bytes(self) -> None:
        registration = sealed_registration()
        comparisons = [comparison(registration, index) for index in range(12)]
        evaluation = _evaluate(registration, comparisons)
        claim = issue_tier4_claim(
            evaluation=evaluation,
            issued_at="2026-09-01T01:00:00Z",
            expires_at="2027-03-01T00:00:00Z",
        )
        self.assertEqual(
            _load("positive/expected-evaluation.json"),
            _evaluation_projection(evaluation),
        )
        self.assertEqual(_load("positive/expected-claim.json"), _claim_projection(claim))
        self.assertEqual("tier4_supported", evaluation["decision"]["status"])
        self.assertNotIn("probability", claim["claim_text"].lower())

    def test_independence_leakage_and_chronology_gates_fail_closed(self) -> None:
        sparse = sealed_registration(blocks=1)
        sparse_comparisons = [comparison(sparse, 0)]
        sparse_evaluation = _evaluate(sparse, sparse_comparisons)
        self.assertEqual(
            _load("sparse/expected-evaluation.json"),
            _evaluation_projection(sparse_evaluation),
        )
        self.assertEqual("evaluated_with_limitations", sparse_evaluation["decision"]["status"])
        self.assertIn("reason-code:minimum-independent-blocks", sparse_evaluation["limitations"])
        with self.assertRaises(ContractError):
            issue_tier4_claim(
                evaluation=sparse_evaluation,
                issued_at="2026-09-01T01:00:00Z",
                expires_at="2027-03-01T00:00:00Z",
            )
        self.assertEqual(3, len(sparse_comparisons[0]["pairwise_comparisons"]))
        self.assertEqual(
            1,
            len({item["block_binding"]["block_id"] for item in sparse_comparisons}),
            "pair inflation must never replace the one independent campaign block",
        )

        registration = sealed_registration()
        observation = observation_fixture(registration)
        observation["holdout_status"] = "eligible_held_out"
        observation["outcome_accessed_at"] = registration["registered_at"]
        observation["observation_sha256"] = sha256_json(
            {**observation, "observation_sha256": None},
        )
        with self.assertRaisesRegex(ContractError, "registered before outcome"):
            validate_validation_observation(observation)

        late_registration = preregistration_fixture()
        late_registration["registered_at"] = "2026-08-03T00:00:00Z"
        holdout_boolean = observation_fixture(
            seal_preregistration(late_registration),
        )
        holdout_boolean["outcome_accessed_at"] = "2026-08-02T00:00:00Z"
        with self.assertRaisesRegex(ContractError, "registered before outcome"):
            validate_validation_observation(holdout_boolean)

        source_hash = observation_fixture()["source"]["source_sha256"]
        unsealed = deepcopy(registration)
        unsealed["prior_outcome_access"] = [{
            "access_sha256": source_hash,
            "accessed_at": "2026-07-31T12:00:00Z",
            "kind": "model-fitting-review",
        }]
        unsealed["registration_sha256"] = None
        fitted_registration = seal_preregistration(unsealed)
        fitted_rows = _gate_observations(
            fitted_registration,
            holdout_status="in_sample",
            design="randomized",
        )
        self.assertTrue(all(
            validate_validation_observation(row)["holdout_status"] == "in_sample"
            for row in fitted_rows
        ))
        _assert_downstream_holdout_gate(
            self,
            fitted_registration,
            fitted_rows,
        )

        observational_rows = _gate_observations(
            registration,
            holdout_status="mismatched",
            design="observational",
        )
        self.assertTrue(all(
            validate_validation_observation(row)["holdout_status"] == "mismatched"
            for row in observational_rows
        ))
        _assert_downstream_holdout_gate(self, registration, observational_rows)

        revealed_early = deepcopy(registration)
        revealed_early["synthetic_surface"]["frozen_at"] = "2026-08-02T00:00:00Z"
        revealed_early["synthetic_surface"]["producer_evidence_sealed_at"] = "2026-08-02T00:00:00Z"
        revealed_early["registration_sha256"] = None
        with self.assertRaises(ContractError):
            seal_preregistration(revealed_early)

    def test_binding_metric_post_hoc_and_repeated_look_matrix(self) -> None:
        registration = sealed_registration()
        comparisons = [comparison(registration, index) for index in range(12)]
        family = _family(registration, comparisons)
        mutations = {
            "metric": lambda item: item["metric_binding"].update(name="post-hoc-metric"),
            "unit": lambda item: item["metric_binding"].update(exposure_unit="click"),
            "window": lambda item: item["metric_binding"].update(measurement_window="2026-q4"),
            "cohort": lambda item: item["panel_binding"].update(panel_id="different-cohort"),
            "placement": lambda item: item["panel_binding"].update(panel_version="different-placement"),
            "hash": lambda item: item["synthetic_result_binding"].update(result_sha256=digest("f")),
        }
        for name, mutate in mutations.items():
            changed = deepcopy(comparisons)
            mutate(changed[0])
            _reseal_comparison(changed[0])
            with self.subTest(name=name):
                result = _evaluate(registration, changed, family)
                self.assertEqual("invalid", result["decision"]["status"])
                with self.assertRaises(ContractError):
                    issue_tier4_claim(
                        evaluation=result,
                        issued_at="2026-09-01T01:00:00Z",
                        expires_at="2027-03-01T00:00:00Z",
                    )
                self.assertEqual(
                    _load("metric-mismatch/expected-error.json")["reason_code"],
                    result["limitations"][0],
                )

        for scope_field in ("cohort_id", "placement"):
            mismatched_observation = observation_fixture()
            mismatched_observation["outcome_scope"][scope_field] = "unregistered-scope"
            _reseal_observation(mismatched_observation)
            with self.subTest(scope_field=scope_field), self.assertRaisesRegex(
                ContractError,
                "claim_scope outcome subset",
            ):
                validate_validation_observation(mismatched_observation)

        post_hoc = deepcopy(registration)
        post_hoc["secondary_metrics"] = [deepcopy(post_hoc["primary_metric"])]
        post_hoc["secondary_metrics"][0]["name"] = "post-hoc-secondary"
        post_hoc["registration_sha256"] = None
        post_hoc = seal_preregistration(post_hoc)
        post_hoc_comparisons = [comparison(post_hoc, index) for index in range(12)]
        post_hoc_comparisons[0]["metric_binding"] = deepcopy(post_hoc["secondary_metrics"][0])
        _reseal_comparison(post_hoc_comparisons[0])
        result = _evaluate(
            post_hoc,
            post_hoc_comparisons,
            _family(post_hoc, [comparison(post_hoc, index) for index in range(12)]),
        )
        self.assertEqual("invalid", result["decision"]["status"])

        unplanned_look = deepcopy(registration)
        unplanned_look["interim_analysis_rules"]["observed_looks"] = 2
        unplanned_look["registration_sha256"] = None
        with self.assertRaisesRegex(ContractError, "unknown fields"):
            seal_preregistration(unplanned_look)

    def test_segment_reversal_ties_indeterminacy_chance_and_holm(self) -> None:
        registration = sealed_registration()
        reversal = [comparison(registration, index, reverse=True) for index in range(12)]
        reversed_evaluation = _evaluate(registration, reversal)
        self.assertEqual(
            _load("segment-reversal/expected-evaluation.json"),
            _evaluation_projection(reversed_evaluation),
        )
        self.assertEqual("tier4_not_supported", reversed_evaluation["decision"]["status"])
        with self.assertRaises(ContractError):
            issue_tier4_claim(
                evaluation=reversed_evaluation,
                issued_at="2026-09-01T01:00:00Z",
                expires_at="2027-03-01T00:00:00Z",
            )

        chance = [
            comparison(registration, index, reverse=index >= 6)
            for index in range(12)
        ]
        self.assertEqual("tier4_not_supported", _evaluate(registration, chance)["decision"]["status"])

        indeterminate = [comparison(registration, index) for index in range(12)]
        for item in indeterminate:
            for pair in item["pairwise_comparisons"]:
                pair["observed_direction"] = "observed_indeterminate"
            _reseal_comparison(item)
        limited = _evaluate(registration, indeterminate)
        self.assertEqual("evaluated_with_limitations", limited["decision"]["status"])
        self.assertIn("reason-code:determinate-pair-coverage", limited["limitations"])

        equivalent = [comparison(registration, index) for index in range(12)]
        for item in equivalent:
            item["observed_ordering"] = [["creative-a", "creative-b", "creative-c"]]
            for pair in item["pairwise_comparisons"]:
                pair["observed_direction"] = "observed_equivalent"
            _reseal_comparison(item)
        with self.assertRaisesRegex(
            ContractError,
            "unusable complete-block statistics",
        ):
            _family(registration, equivalent)

        comparisons = [comparison(registration, index) for index in range(12)]
        incomplete = _family(registration, comparisons)
        incomplete["complete"] = False
        incomplete["family_sha256"] = sha256_json(
            {**incomplete, "family_sha256": None},
        )
        limited = _evaluate(registration, comparisons, incomplete)
        self.assertEqual("invalid", limited["decision"]["status"])
        self.assertIn("reason-code:untrusted-or-invalid-claim-family", limited["limitations"])
        self.assertIn("reason-code:incomplete-claim-family", limited["limitations"])

        holm = _family(registration, comparisons)
        holm["adjusted_p_values"] = [1.0]
        holm["family_sha256"] = sha256_json({**holm, "family_sha256": None})
        failed = _evaluate(registration, comparisons, holm)
        self.assertEqual("evaluated_with_limitations", failed["decision"]["status"])
        self.assertIn("reason-code:holm-adjusted-failure", failed["limitations"])

    def test_sparse_metric_boundaries_are_fictional_aggregate_only(self) -> None:
        for relative in (
            "sparse/zero-success-binary.json",
            "sparse/all-success-binary.json",
            "sparse/one-sample-continuous.json",
            "sparse/zero-event-rate.json",
        ):
            with self.subTest(relative=relative):
                fixture = _load(relative)
                document = observation_fixture_for(
                    metric_family=fixture["metric_family"],
                    aggregate=fixture["aggregate"],
                )
                normalized = normalize_observation(document)
                if relative.endswith("one-sample-continuous.json"):
                    self.assertEqual("limited", normalized.support_status)
                    self.assertIsNone(normalized.lower)
                elif relative.endswith("all-success-binary.json"):
                    self.assertLess(normalized.lower, 1.0)
                else:
                    self.assertGreater(normalized.upper, 0.0)
                self.assertNotIn("person_id", json.dumps(document))
        sparse_rate = normalize_observation(observation_fixture_for(
            metric_family="event_rate",
            aggregate={"event_count": 1, "exposure_time": 1000.0},
        ))
        self.assertGreater(sparse_rate.upper, sparse_rate.point)

        observational = observation_fixture()
        observational["assignment"]["design"] = "observational"
        observational["holdout_status"] = "mismatched"
        _reseal_observation(observational)
        self.assertEqual(
            "mismatched",
            validate_validation_observation(observational)["holdout_status"],
        )

    def test_negative_package_and_complete_claim_lifecycle_matrix(self) -> None:
        from conformance.test_tier4_validation_package import Tier4ValidationPackageTests

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            helper = Tier4ValidationPackageTests()
            panel = helper._panel(root / "panel")
            negative = build_validation_package(
                inputs=helper._inputs(root / "negative-inputs", panel, negative=True),
                panel_package_path=panel,
                output_dir=root / "negative-output",
            )
            self.assertEqual("valid", validate_validation_package(negative)["status"])

            package = build_validation_package(
                inputs=helper._inputs(root / "positive-inputs", panel),
                panel_package_path=panel,
                output_dir=root / "positive-output",
            )
            active_library = root / "active-library"
            active = register_validation_package(
                package,
                library_root=active_library,
                registered_at="2026-09-02T00:00:00Z",
            )["claim"]
            self.assertEqual(
                active["claim_id"],
                current_claim(
                    active["panel_id"],
                    active["panel_version"],
                    active["claim_scope_sha256"],
                    library_root=active_library,
                    as_of="2026-10-01T00:00:00Z",
                )["claim"]["claim_id"],
            )

            for event_type in ("expired", "withdrawn", "invalidated"):
                library = root / f"{event_type}-library"
                claim = register_validation_package(
                    package,
                    library_root=library,
                    registered_at="2026-09-02T00:00:00Z",
                )["claim"]
                before = current_claim(
                    claim["panel_id"],
                    claim["panel_version"],
                    claim["claim_scope_sha256"],
                    library_root=library,
                    as_of="2027-02-28T00:00:00Z" if event_type == "expired" else "2026-09-30T00:00:00Z",
                )
                self.assertEqual(claim["claim_id"], before["claim"]["claim_id"])
                event = append_claim_lifecycle_event(
                    claim_id=claim["claim_id"],
                    event_type=event_type,
                    effective_at=claim["expires_at"] if event_type == "expired" else "2026-10-01T00:00:00Z",
                    actor_id="maintainer-001",
                    reason=f"Fictional {event_type} proof.",
                    evidence_sha256=[claim["claim_sha256"]],
                    replacement_claim_id=None,
                    library_root=library,
                )
                self.assertEqual(claim["claim_id"], event["claim_id"])
                self.assertEqual(event_type, event["event_type"])
                self.assertEqual(
                    sha256_json({**event, "event_sha256": None}),
                    event["event_sha256"],
                )
                shown = show_claim(claim["claim_id"], library_root=library)
                self.assertEqual([event], shown["events"])
                self.assertEqual(1, shown["claim"]["event_count"])
                self.assertEqual(
                    event["event_sha256"],
                    shown["claim"]["event_head_sha256"],
                )
                after_append_before_effective = current_claim(
                    claim["panel_id"],
                    claim["panel_version"],
                    claim["claim_scope_sha256"],
                    library_root=library,
                    as_of="2027-02-28T23:59:59Z" if event_type == "expired" else "2026-09-30T23:59:59Z",
                )
                self.assertEqual(
                    claim["claim_id"],
                    after_append_before_effective["claim"]["claim_id"],
                )
                with self.assertRaises(LibraryNotFoundError):
                    current_claim(
                        claim["panel_id"],
                        claim["panel_version"],
                        claim["claim_scope_sha256"],
                        library_root=library,
                        as_of="2027-04-01T00:00:00Z" if event_type == "expired" else "2026-10-02T00:00:00Z",
                    )

            supersession_inputs = helper._inputs(
                root / "supersession-inputs",
                panel,
                registration_id="validation-q3-replacement",
            )
            replacement_package = build_validation_package(
                inputs=supersession_inputs,
                panel_package_path=panel,
                output_dir=root / "supersession-output",
            )
            supersession_library = root / "supersession-library"
            original = register_validation_package(
                package,
                library_root=supersession_library,
                registered_at="2026-09-02T00:00:00Z",
            )["claim"]
            replacement = register_validation_package(
                replacement_package,
                library_root=supersession_library,
                registered_at="2026-10-01T00:00:00Z",
            )["claim"]
            superseded_event = append_claim_lifecycle_event(
                claim_id=original["claim_id"],
                event_type="superseded",
                effective_at="2026-10-01T00:00:00Z",
                actor_id="maintainer-001",
                reason="A separately validated fictional replacement exists.",
                evidence_sha256=[replacement["claim_sha256"]],
                replacement_claim_id=replacement["claim_id"],
                library_root=supersession_library,
            )
            self.assertEqual("superseded", superseded_event["event_type"])
            self.assertEqual(original["claim_id"], superseded_event["claim_id"])
            self.assertEqual(
                replacement["claim_id"],
                superseded_event["replacement_claim_id"],
            )
            self.assertEqual(
                [replacement["claim_sha256"]],
                superseded_event["evidence_sha256"],
            )
            self.assertEqual(
                sha256_json({**superseded_event, "event_sha256": None}),
                superseded_event["event_sha256"],
            )
            shown_original = show_claim(
                original["claim_id"],
                library_root=supersession_library,
            )
            self.assertEqual([superseded_event], shown_original["events"])
            self.assertEqual(1, shown_original["claim"]["event_count"])
            self.assertEqual(
                superseded_event["event_sha256"],
                shown_original["claim"]["event_head_sha256"],
            )
            pre_supersession = current_claim(
                original["panel_id"],
                original["panel_version"],
                original["claim_scope_sha256"],
                library_root=supersession_library,
                as_of="2026-09-30T23:59:59Z",
            )
            self.assertEqual(
                original["claim_id"],
                pre_supersession["claim"]["claim_id"],
            )
            selected = current_claim(
                original["panel_id"],
                original["panel_version"],
                original["claim_scope_sha256"],
                library_root=supersession_library,
                as_of="2026-10-02T00:00:00Z",
            )
            self.assertEqual(replacement["claim_id"], selected["claim"]["claim_id"])

    def test_v2_bytes_and_all_protected_ad_testing_outputs_remain_literal(self) -> None:
        from conformance.test_audience_package import AudiencePackageTest
        from conformance.test_population_runtime_golden_paths import (
            PopulationRuntimeGoldenPathTests,
            canonical_bytes,
        )
        with tempfile.TemporaryDirectory() as temporary:
            harness = AudiencePackageTest()
            harness.setUp()
            built = harness._build(Path(temporary))
            self.assertEqual(EXPECTED_V2_GENERATOR_SHA256, built.package_zip_sha256)

        try:
            import scipy  # noqa: F401
        except ModuleNotFoundError:
            self.skipTest("SciPy is unavailable; CI exercises protected numerical snapshots")
        from conformance.test_assignments import AssignmentTests
        from conformance.test_maxdiff import (
            MaxDiffMaximumDesignBenchmark,
            PROTECTED_MAXIMUM_CAPACITY,
            PROTECTED_MAXIMUM_SHA256,
        )
        from conformance.test_planning import PlanningTests
        from conformance import test_v3_dispatch_compatibility as dispatch_contract
        from conformance import test_v3_profile_rosters as roster_contract
        from audience_lab import dispatch as dispatch_module

        protected = PopulationRuntimeGoldenPathTests()
        protected.setUp()
        protected.test_v2_bytes_and_all_protected_worker_outputs_are_unchanged()
        snapshots = protected._protected_snapshots()
        self.assertEqual(
            EXPECTED_PROTECTED_PRE_C1_SHA256,
            {
                name: hashlib.sha256(
                    value if isinstance(value, bytes) else canonical_bytes(value),
                ).hexdigest()
                for name, value in snapshots.items()
            },
        )
        self.assertEqual(EXPECTED_MAXIMUM_CAPACITY, PROTECTED_MAXIMUM_CAPACITY)
        self.assertEqual(EXPECTED_MAXIMUM_CAPACITY_SHA256, PROTECTED_MAXIMUM_SHA256)
        maximum = MaxDiffMaximumDesignBenchmark()
        v2_plan, v3_plan, v3_replay, _v3_envelope = maximum._production_maximum_plans()
        self.assertEqual(EXPECTED_MAXIMUM_CAPACITY, v2_plan["synthetic_replicate_capacity"])
        self.assertEqual(
            EXPECTED_MAXIMUM_CAPACITY,
            v3_plan["synthetic_replicate_capacity"],
        )
        self.assertEqual(
            v3_plan["audience_profile_rosters"],
            v3_replay["audience_profile_rosters"],
        )
        screening_jobs = v2_plan["assignment"]["synthetic_replicate_jobs"]
        self.assertEqual(225, len(screening_jobs))
        self.assertEqual(
            225,
            len({job["synthetic_replicate_id"] for job in screening_jobs}),
        )
        for stage, expected_count in (
            ("screening", 225),
            ("boundary_reserve", 16),
            ("finalist_reserve", 8),
        ):
            assignments = v3_plan["audience_profile_rosters"][stage]["assignments"]
            self.assertEqual(expected_count, len(assignments))
            self.assertEqual(
                expected_count,
                len({item["slot_id"] for item in assignments}),
            )
            self.assertTrue(all(item["grounded_profile_id"] for item in assignments))
            self.assertEqual(
                expected_count,
                len({
                    (item["slot_id"], item["grounded_profile_id"])
                    for item in assignments
                }),
            )

        with tempfile.TemporaryDirectory() as dispatch_temporary:
            dispatch_root = Path(dispatch_temporary)
            roster_harness = roster_contract.V3ProfileRosterTests()
            roster_harness.setUp()
            package_path, _run_path, resolution_path = roster_harness._resolved_run(
                dispatch_root,
            )
            dispatch_plan = maximum._run_production_maximum_plan(
                root=dispatch_root,
                package_path=package_path,
                resolution_path=resolution_path,
                output_name="production-worker-proof.json",
            )
            stage_jobs: list[dict[str, object]] = []
            screening_ids = [
                item["slot_id"]
                for item in dispatch_plan["audience_profile_rosters"]["screening"]["assignments"]
            ]
            screening = dispatch_module._enrich_v3_assignment_jobs(
                dispatch_plan,
                dispatch_contract._dispatch_context(
                    dispatch_plan, "screening_response",
                ),
                manifest=None,
                audience_resolution=resolution_path,
                authority=dispatch_plan,
                selected_slot_ids=screening_ids,
            )
            stage_jobs.extend(screening["synthetic_replicate_jobs"])

            boundary_authority = dispatch_contract._boundary_authority(dispatch_plan)
            boundary_ids = [
                item["slot_id"]
                for item in dispatch_plan["audience_profile_rosters"]["boundary_reserve"]["assignments"]
            ]
            boundary_context = dispatch_contract._dispatch_context(
                dispatch_plan, "boundary_response",
            )
            boundary_context["creative_prompts"].update({
                f"creative-{index}": f"Review creative-{index}."
                for index in range(1, 4)
            })
            boundary = dispatch_module._enrich_v3_assignment_jobs(
                boundary_authority,
                boundary_context,
                manifest=dispatch_plan,
                audience_resolution=resolution_path,
                authority=dispatch_plan,
                selected_slot_ids=boundary_ids,
            )
            stage_jobs.extend(boundary["synthetic_replicate_jobs"])

            manifest = dispatch_contract._manifest_from_plan(dispatch_plan)
            creative_ids = sorted(manifest["outputs"]["creative_asset_hashes"])
            approval = {
                "study_id": dispatch_plan["study_id"],
                "method": dispatch_plan["method"],
                "approved_finalist_ids": creative_ids[
                    : dispatch_plan["requested_shortlist_size"]
                ],
                "roster_decision": {
                    "status": "approved",
                    "approved_at": "2026-07-25T12:00:00Z",
                    "approved_by": "study owner",
                    "override": False,
                    "changed_after_saliency_reveal": False,
                },
            }
            finalist_ids = [
                item["slot_id"]
                for item in dispatch_plan["audience_profile_rosters"]["finalist_reserve"]["assignments"]
            ]
            finalist = dispatch_module._enrich_v3_assignment_jobs(
                approval,
                dispatch_contract._dispatch_context(
                    dispatch_plan, "finalist_response",
                ),
                manifest=manifest,
                audience_resolution=resolution_path,
                authority=manifest,
                selected_slot_ids=finalist_ids,
            )
            stage_jobs.extend(finalist["synthetic_replicate_jobs"])
        self.assertEqual(
            (225, 16, 8, 249),
            (
                len(screening["synthetic_replicate_jobs"]),
                len(boundary["synthetic_replicate_jobs"]),
                len(finalist["synthetic_replicate_jobs"]),
                len(stage_jobs),
            ),
        )
        worker_identity_fields = (
            "synthetic_replicate_id",
            "response_id",
            "dispatch_id",
            "audience_slot_id",
        )
        for field in worker_identity_fields:
            self.assertEqual(
                249,
                len({job[field] for job in stage_jobs}),
            )
        roster_by_slot = {}
        for stage in ("screening", "boundary_reserve", "finalist_reserve"):
            roster_by_slot.update({
                item["slot_id"]: item
                for item in dispatch_plan["audience_profile_rosters"][stage]["assignments"]
            })
        self.assertEqual(249, len(roster_by_slot))
        for job in stage_jobs:
            frozen = roster_by_slot[job["audience_slot_id"]]
            self.assertEqual("isolated", job["worker_context_isolation"])
            self.assertEqual(frozen["grounded_profile_id"], job["grounded_profile_id"])
            self.assertEqual(
                frozen["profile_snapshot_sha256"],
                job["profile_snapshot_sha256"],
            )
        self.assertEqual(
            249,
            len({job["dispatch_id"] for job in stage_jobs}),
            "each synthetic panelist job must project to one isolated worker identity",
        )
        planning_result = unittest.TestResult()
        PlanningTests("test_boundary_reserve_cannot_consume_finalist_reserve").run(
            planning_result,
        )
        self.assertTrue(planning_result.wasSuccessful(), planning_result.errors)
        assignment_result = unittest.TestResult()
        AssignmentTests("test_reserve_slot_ids_keep_existing_wave_position_and_finalist_order").run(
            assignment_result,
        )
        self.assertTrue(assignment_result.wasSuccessful(), assignment_result.errors)
        assignment_result = unittest.TestResult()
        AssignmentTests("test_assignments_are_deterministic_and_four_item").run(
            assignment_result,
        )
        self.assertTrue(assignment_result.wasSuccessful(), assignment_result.errors)


if __name__ == "__main__":
    unittest.main()
