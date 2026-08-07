"""Integrated golden paths for the synthetic persona calibration sandbox."""

from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import platform
import shutil
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "skills" / "audience-panel-builder" / "scripts"
AD_LAB_SCRIPTS = ROOT / "skills" / "audience-ad-testing-lab" / "scripts"
FIXTURES = ROOT / "conformance" / "fixtures" / "experimental-calibration"
V2_FIXTURES = ROOT / "conformance" / "fixtures" / "audience-research"

import sys

sys.path[:0] = [str(SCRIPTS), str(AD_LAB_SCRIPTS)]

from audience_lab.audience_library import LibraryError, register_package  # noqa: E402
from audience_lab.audience_package import build_audience_package  # noqa: E402
from audience_lab.audience_research_v3 import validate_saved_panel_v3  # noqa: E402
from audience_panel_builder.common import (  # noqa: E402
    ContractError,
    canonical_json_bytes,
    sha256_json,
)
from audience_panel_builder.population.experimental_calibration.adapters import (  # noqa: E402
    normalize_platform_export,
)
from audience_panel_builder.population.experimental_calibration.attributes import (  # noqa: E402
    build_creative_attribute_registry,
)
from audience_panel_builder.population.experimental_calibration.candidate import (  # noqa: E402
    materialize_sandbox_candidate,
    publish_sandbox_candidate_bundle,
)
from audience_panel_builder.population.experimental_calibration.contracts import (  # noqa: E402
    validate_experimental_proposal,
    validate_outcome_observation,
    validate_sandbox_candidate_binding,
)
from audience_panel_builder.population.experimental_calibration.diagnosis import (  # noqa: E402
    _estimate_blocked_contrasts,
    diagnose_persona_behavior,
)
from audience_panel_builder.population.experimental_calibration.exercise import (  # noqa: E402
    build_synthetic_panel_exercise,
)
from audience_panel_builder.population.experimental_calibration.proposal import (  # noqa: E402
    build_experimental_proposal,
)
from conformance.experimental_calibration_fixtures import (  # noqa: E402
    candidate_base_panel_fixture,
    creative_attribute_inputs,
    diagnosis_inputs_fixture,
    evaluation_inputs_fixture,
    evaluation_fixture,
    exercise_inputs_fixture,
    outcome_observation_fixture,
    proposal_inputs_fixture,
    raw_platform_export_fixture,
    rehash,
    valid_candidate_inputs,
)
from conformance.experimental_calibration_coverage import (  # noqa: E402
    execute_coverage_matrix,
)
from experimental_persona_calibration_oracle import (  # noqa: E402
    validate_synthetic_evaluation,
)
from experimental_persona_calibration_oracle.evaluator import (  # noqa: E402
    SealedHoldoutFailure,
    evaluate_synthetic_study,
)
from experimental_persona_calibration_oracle.sandbox import (  # noqa: E402
    run_engine_in_private_stage,
)


_EXPECTED_GOLDENS = (
    ("null-effect", "no_change", False),
    ("known-proof-need-miss", "profile_snapshot_update", True),
    ("non-identifiable-twins", "abstain", False),
    ("one-campaign-only", "insufficient_evidence", False),
    ("observational-confounding", "no_change", False),
    ("platform-interaction", "platform_specific_no_pooling", False),
    ("denominator-mismatch", "incompatible", False),
    ("attribution-mismatch", "incompatible", False),
    ("late-maturation", "early_evidence_blocked", False),
    ("modeled-fractional", "values_preserved", False),
    ("suppressed-missing", "missing_not_zero", False),
    ("breakdown-double-count", "duplicate_prevented", False),
    ("block-reversal", "block_aware", False),
    ("creative-attribute-ambiguity", "invalid_evidence", False),
    ("duplicate-evidence", "hard_failure", False),
    ("hidden-oracle-leak", "hard_failure", False),
    ("structural-change-request", "hard_failure", False),
    ("multiple-hypotheses", "abstain", False),
    ("candidate-extra-diff", "hard_failure", False),
    ("candidate-registration", "hard_failure", True),
    ("existing-output", "no_clobber", False),
    ("reversed-row-order", "deterministic", False),
    ("sealed-holdout-reuse", "hard_failure", False),
    ("base-panel-package-bytes", "preserved", False),
    ("ad-testing-output-bytes", "preserved", False),
    ("nonlinear-saturation", "sensitivity_reported", False),
    ("delayed-censored-outcomes", "ineligible_until_final", False),
    ("zero-inflated-value", "honest_abstention", False),
    ("production-library-snapshot", "preserved", False),
)


def _tree_digest(root: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(item for item in root.rglob("*") if item.is_file()):
        digest.update(path.relative_to(root).as_posix().encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
    return "sha256:" + digest.hexdigest()


def _tree_bytes(root: Path) -> dict[str, bytes]:
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def _bytes_sha256(raw: bytes) -> str:
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def _scenario_result(
    evaluation: dict[str, object],
    scenario_id: str,
) -> dict[str, object]:
    family = next(
        row
        for row in evaluation["scenario_family_results"]
        if row["scenario_family_id"] == scenario_id
    )
    return family["scenario_results"][0]


def _oracle_document(
    evaluation_inputs: dict[str, object],
    scenario_id: str,
) -> dict[str, object]:
    return next(
        row
        for row in evaluation_inputs["oracle_documents"]
        if row["scenario_id"] == scenario_id
    )


def _publish_candidate(
    materialized: dict[str, object],
    inputs: dict[str, object],
    output_dir: Path,
) -> Path:
    return publish_sandbox_candidate_bundle(
        materialized=materialized,
        study_manifest=inputs["study_manifest"],
        scenario_manifests=inputs["scenario_manifests"],
        experiment_designs=inputs["experiment_designs"],
        diagnosis=inputs["diagnosis"],
        attribute_registry=inputs["attribute_registry"],
        evidence_library_snapshot=inputs["evidence_library_snapshot"],
        evidence_head_receipt=inputs["evidence_head_receipt"],
        alternative_causes=inputs["alternative_causes"],
        output_dir=output_dir,
    )


def _normalize_scenario(
    *,
    scenario_id: str,
    platform_name: str,
    study_manifest: dict[str, object],
    registry: dict[str, object],
) -> list[dict[str, object]]:
    raw = raw_platform_export_fixture(platform_name, scenario_id)
    return normalize_platform_export(
        platform=platform_name,
        raw_export_bytes=raw,
        source_sha256=_bytes_sha256(raw),
        study_manifest=study_manifest,
        creative_attribute_registry=registry,
    )


def _hard_failure(
    operation,
    *,
    expected_exceptions: tuple[type[BaseException], ...] = (ContractError,),
) -> tuple[str, BaseException]:
    try:
        operation()
    except expected_exceptions as exc:
        return "hard_failure", exc
    raise AssertionError("golden rejection path unexpectedly succeeded")


_CANDIDATE_BUNDLE_FILES = {
    "README.txt",
    "base-persona-authoring-projection.json",
    "base-persona-snapshot.json",
    "bundle-manifest.json",
    "candidate-audience-panel.json",
    "candidate-persona-authoring-projection.json",
    "candidate-persona-snapshot.json",
    "experimental-candidate-binding.json",
    "experimental-proposal.json",
    "persona-behavior-diff.json",
    "standalone-panel-validation.json",
}
_CANDIDATE_FILE_MARKERS = _CANDIDATE_BUNDLE_FILES - {"README.txt"}


def _observe_closed_candidate_bundles(output_root: Path) -> tuple[Path, ...]:
    """Return only complete, self-authenticating sandbox candidate bundles."""

    manifests = sorted(output_root.rglob("bundle-manifest.json"))
    marker_paths = {
        path
        for name in _CANDIDATE_FILE_MARKERS
        for path in output_root.rglob(name)
    }
    if marker_paths and not manifests:
        raise AssertionError("candidate files exist without a bundle manifest")

    bundles: list[Path] = []
    bound_markers: set[Path] = set()
    for manifest_path in manifests:
        bundle = manifest_path.parent
        try:
            manifest = json.loads(manifest_path.read_bytes())
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise AssertionError("candidate bundle manifest is unreadable") from exc
        supplied = manifest.get("bundle_manifest_sha256")
        unhashed = deepcopy(manifest)
        unhashed["bundle_manifest_sha256"] = None
        if (
            set(manifest) != {
                "schema_version",
                "candidate_id",
                "registration_permitted",
                "production_package_manifest_present",
                "production_package_graph_present",
                "files",
                "bundle_manifest_sha256",
            }
            or manifest.get("schema_version")
            != "experimental-persona-candidate-bundle-manifest-v1"
            or sha256_json(unhashed) != supplied
            or manifest.get("registration_permitted") is not False
            or manifest.get("production_package_manifest_present") is not False
            or manifest.get("production_package_graph_present") is not False
        ):
            raise AssertionError("candidate bundle manifest is not authentic")

        files = manifest.get("files")
        if not isinstance(files, list):
            raise AssertionError("candidate bundle file bindings are missing")
        bound_names = {
            row.get("path")
            for row in files
            if isinstance(row, dict)
        }
        expected_bound = _CANDIDATE_BUNDLE_FILES - {"bundle-manifest.json"}
        actual_names = {
            path.relative_to(bundle).as_posix()
            for path in bundle.rglob("*")
            if path.is_file()
        }
        if (
            len(files) != len(expected_bound)
            or bound_names != expected_bound
            or actual_names != _CANDIDATE_BUNDLE_FILES
        ):
            raise AssertionError("candidate bundle file inventory is incomplete")

        for row in files:
            if not isinstance(row, dict) or set(row) != {
                "path",
                "sha256",
                "byte_count",
            }:
                raise AssertionError("candidate bundle file binding is not closed")
            path = bundle / str(row["path"])
            raw = path.read_bytes()
            if (
                len(raw) != row["byte_count"]
                or _bytes_sha256(raw) != row["sha256"]
            ):
                raise AssertionError("candidate bundle file bytes do not match")

        binding = json.loads(
            (bundle / "experimental-candidate-binding.json").read_bytes()
        )
        validated = validate_sandbox_candidate_binding(binding)
        panel = validate_saved_panel_v3(
            json.loads(
                (bundle / "candidate-audience-panel.json").read_bytes()
            )
        )
        if validated["candidate_id"] != manifest.get("candidate_id"):
            raise AssertionError("candidate bundle identity does not match")
        if (
            validated["candidate_panel_binding"]["panel_id"]
            != panel["panel_id"]
            or validated["candidate_panel_binding"]["panel_version"]
            != panel["version"]
            or validated["candidate_panel_binding"]["panel_sha256"]
            != sha256_json(panel)
        ):
            raise AssertionError("candidate bundle panel binding does not match")
        bound_markers.update(
            path for path in bundle.rglob("*") if path.is_file()
        )
        bundles.append(bundle)

    if marker_paths - bound_markers:
        raise AssertionError("candidate files exist outside a closed bundle")
    if len(bundles) > 1:
        raise AssertionError("golden row created more than one candidate bundle")
    return tuple(bundles)


def _assert_observed_rows(
    rows: list[dict[str, object]],
    expected: tuple[tuple[str, str, bool], ...] = _EXPECTED_GOLDENS,
) -> None:
    expected_by_id = {
        scenario_id: (result, candidate_created)
        for scenario_id, result, candidate_created in expected
    }
    if set(expected_by_id) != {row["scenario_id"] for row in rows}:
        raise AssertionError("observed golden rows are missing or duplicated")
    for row in rows:
        expected_result, expected_candidate = expected_by_id[row["scenario_id"]]
        if row["actual_result"] != expected_result:
            raise AssertionError(
                f"{row['scenario_id']} observed {row['actual_result']!r}, "
                f"expected {expected_result!r}"
            )
        if row["candidate_created"] is not expected_candidate:
            raise AssertionError(
                f"{row['scenario_id']} candidate state did not match"
            )
        if not row["base_panel_unchanged"]:
            raise AssertionError(f"{row['scenario_id']} changed base-panel bytes")
        if row["active_panel_mutated"]:
            raise AssertionError(
                f"{row['scenario_id']} changed the production audience library"
            )


class ExperimentalCalibrationGoldenPathTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.evaluation = evaluation_fixture()
        cls.evaluation_inputs = evaluation_inputs_fixture()
        cls.exercise_inputs = exercise_inputs_fixture()

    def _observe_golden_path(
        self,
        scenario_id: str,
        *,
        output_root: Path,
        base_panel: dict[str, object],
        production_library: Path,
        package_zip: Path,
    ) -> tuple[str, object | None]:
        evaluated_ids = {
            row["scenario_family_id"]
            for row in self.evaluation["scenario_family_results"]
        }
        scenario = (
            _scenario_result(self.evaluation, scenario_id)
            if scenario_id in evaluated_ids
            else {}
        )
        validate_synthetic_evaluation(deepcopy(self.evaluation))

        if scenario_id == "null-effect":
            diagnosis = diagnose_persona_behavior(
                **diagnosis_inputs_fixture(scenario_id="null-effect")
            )
            return {
                "no_repeatable_miss": "no_change"
            }[diagnosis["decision"]], diagnosis

        if scenario_id == "known-proof-need-miss":
            inputs = valid_candidate_inputs()
            inputs["base_panel"] = base_panel
            candidate = materialize_sandbox_candidate(**inputs)
            _publish_candidate(
                candidate,
                inputs,
                output_root / "candidate-bundle",
            )
            operation = candidate["candidate_binding"]["applied_operation"][
                "operation_type"
            ]
            return str(operation), candidate

        if scenario_id == "non-identifiable-twins":
            rows = [
                _scenario_result(self.evaluation, twin_id)
                for twin_id in (
                    "non-identifiable-twin-a",
                    "non-identifiable-twin-b",
                )
            ]
            actions = {row["actual_action"] for row in rows}
            families = {row["epistemic_family_id"] for row in rows}
            if families != {"non-identifiable-twins"}:
                raise AssertionError("twin runtime results lost epistemic grouping")
            return actions.pop(), rows

        if scenario_id == "one-campaign-only":
            diagnosis = diagnose_persona_behavior(
                **diagnosis_inputs_fixture(experiment_limit=1)
            )
            return str(diagnosis["decision"]), diagnosis

        if scenario_id == "observational-confounding":
            inputs = diagnosis_inputs_fixture(evidence_variant="observational")
            diagnosis = diagnose_persona_behavior(**inputs)
            policy = inputs["study_manifest"]["diagnosis_method"][
                "observational_policy"
            ]
            return {
                ("insufficient_evidence", "descriptive_only"): "no_change"
            }[(diagnosis["decision"], policy)], diagnosis

        manifest = self.evaluation_inputs["study_manifest"]
        registry = build_creative_attribute_registry(
            **creative_attribute_inputs()
        )

        if scenario_id == "platform-interaction":
            rows = [
                row
                for platform_name in ("meta", "google", "linkedin", "tiktok")
                for row in _normalize_scenario(
                    scenario_id=scenario_id,
                    platform_name=platform_name,
                    study_manifest=manifest,
                    registry=registry,
                )
            ]
            platforms = {row["source"]["platform"] for row in rows}
            return {
                (4, "abstain"): "platform_specific_no_pooling"
            }[(len(platforms), scenario["actual_action"])], [scenario, *rows]

        if scenario_id == "denominator-mismatch":
            rows = _normalize_scenario(
                scenario_id="denominator-trap",
                platform_name="tiktok",
                study_manifest=manifest,
                registry=registry,
            )
            denominator_kinds = {
                denominator["denominator_kind"]
                for row in rows
                for denominator in row["denominators"]
            }
            evaluated = _scenario_result(self.evaluation, "denominator-trap")
            oracle = _oracle_document(
                self.evaluation_inputs,
                "denominator-trap",
            )
            return {
                (True, "abstain", "denominator-trap"): "incompatible"
            }[(
                len(denominator_kinds) > 1,
                evaluated["actual_action"],
                oracle["failure_mechanism"]["kind"],
            )], [evaluated, *rows]

        if scenario_id == "attribution-mismatch":
            rows = [
                row
                for platform_name in ("meta", "google", "linkedin", "tiktok")
                for row in _normalize_scenario(
                    scenario_id=scenario_id,
                    platform_name=platform_name,
                    study_manifest=manifest,
                    registry=registry,
                )
            ]
            models = {
                row["measurement_definition"]["attribution_model"]
                for row in rows
            }
            oracle = _oracle_document(self.evaluation_inputs, scenario_id)
            return {
                (True, "abstain", "attribution-mismatch"): "incompatible"
            }[(
                len(models) > 1,
                scenario["actual_action"],
                oracle["failure_mechanism"]["kind"],
            )], [scenario, *rows]

        if scenario_id == "late-maturation":
            inputs = diagnosis_inputs_fixture(evidence_variant="recent")
            diagnosis = diagnose_persona_behavior(**inputs)
            maturity = inputs["study_manifest"]["diagnosis_method"][
                "maturity_policy"
            ]
            return {
                ("insufficient_evidence", "finalized_only"):
                    "early_evidence_blocked"
            }[(diagnosis["decision"], maturity)], diagnosis

        if scenario_id == "modeled-fractional":
            rows = _normalize_scenario(
                scenario_id=scenario_id,
                platform_name="google",
                study_manifest=manifest,
                registry=registry,
            )
            counts = [
                event["count"]
                for row in rows
                for event in row["outcome_events"]
                if isinstance(event["count"], float)
            ]
            return {
                True: "values_preserved"
            }[any(not value.is_integer() for value in counts)], rows

        if scenario_id == "suppressed-missing":
            rows = _normalize_scenario(
                scenario_id=scenario_id,
                platform_name="linkedin",
                study_manifest=manifest,
                registry=registry,
            )
            states = {
                row["completeness"]["metric_state"]
                for row in rows
            }
            null_states = {
                row["completeness"]["metric_state"]
                for row in rows
                if any(
                    event["metric_id"] == "total_conversions"
                    and event["count"] is None
                    for event in row["outcome_events"]
                )
            }
            return {
                True: "missing_not_zero"
            }[{"missing", "suppressed"} <= states & null_states], rows

        if scenario_id == "breakdown-double-count":
            document = json.loads(
                raw_platform_export_fixture("meta", scenario_id)
            )
            document["rows"].append(deepcopy(document["rows"][0]))
            raw = canonical_json_bytes(document)
            try:
                normalize_platform_export(
                    platform="meta",
                    raw_export_bytes=raw,
                    source_sha256=_bytes_sha256(raw),
                    study_manifest=manifest,
                    creative_attribute_registry=registry,
                )
            except ContractError as exc:
                return "duplicate_prevented", exc
            raise AssertionError("duplicate adapter row was accepted")

        if scenario_id == "block-reversal":
            inputs = diagnosis_inputs_fixture()["study_manifest"]
            within = [9 / 10 - 80 / 100, 10 / 100 - 0 / 10]
            pooled = (9 + 10) / 110 - (80 + 0) / 110
            _, combined = _estimate_blocked_contrasts(
                {
                    ("experiment-one", "campaign-one"): within * 3,
                    ("experiment-two", "campaign-two"): within * 3,
                },
                diagnosis_method=inputs["diagnosis_method"],
                monte_carlo_error_targets=inputs[
                    "monte_carlo_error_targets"
                ],
            )
            return {
                True: "block_aware"
            }[combined["point_estimate"] > 0 > pooled], combined

        if scenario_id == "creative-attribute-ambiguity":
            oracle = _oracle_document(self.evaluation_inputs, scenario_id)
            observed = (
                oracle["failure_mechanism"]["kind"],
                scenario["actual_action"],
            )
            return {
                ("creative-attribute-ambiguity", "abstain"):
                    "invalid_evidence"
            }[observed], scenario

        if scenario_id == "duplicate-evidence":
            document = json.loads(
                raw_platform_export_fixture("meta", scenario_id)
            )
            document["rows"].append(deepcopy(document["rows"][0]))
            raw = canonical_json_bytes(document)
            return _hard_failure(
                lambda: normalize_platform_export(
                    platform="meta",
                    raw_export_bytes=raw,
                    source_sha256=_bytes_sha256(raw),
                    study_manifest=manifest,
                    creative_attribute_registry=registry,
                )
            )

        if scenario_id == "hidden-oracle-leak":
            observation = outcome_observation_fixture()
            observation["hidden_oracle"] = {"effect": 1}
            observation = rehash(observation, "observation_sha256")
            return _hard_failure(
                lambda: validate_outcome_observation(observation)
            )

        if scenario_id == "structural-change-request":
            proposal = build_experimental_proposal(
                **proposal_inputs_fixture()
            )
            proposal["operation"]["changed_fields"] = ["planned_weight"]
            proposal = rehash(proposal, "proposal_sha256")
            return _hard_failure(
                lambda: validate_experimental_proposal(proposal)
            )

        if scenario_id == "multiple-hypotheses":
            diagnosis = diagnose_persona_behavior(
                **diagnosis_inputs_fixture(second_hypothesis=True)
            )
            return {
                ("invalid_evidence", "abstain"): "abstain"
            }[(diagnosis["decision"], scenario["actual_action"])], [
                diagnosis,
                scenario,
            ]

        if scenario_id == "candidate-extra-diff":
            inputs = valid_candidate_inputs()
            inputs["base_panel"] = base_panel
            candidate = materialize_sandbox_candidate(**inputs)[
                "candidate_binding"
            ]
            candidate["allowed_diff"]["changed_paths"].append(
                "$.structural_composition"
            )
            candidate = rehash(candidate, "candidate_binding_sha256")
            return _hard_failure(
                lambda: validate_sandbox_candidate_binding(candidate)
            )

        if scenario_id == "candidate-registration":
            inputs = valid_candidate_inputs()
            inputs["base_panel"] = base_panel
            materialized = materialize_sandbox_candidate(**inputs)
            bundle = _publish_candidate(
                materialized,
                inputs,
                output_root / "candidate-bundle",
            )
            return _hard_failure(
                lambda: register_package(
                    bundle,
                    library_root=production_library,
                ),
                expected_exceptions=(LibraryError,),
            )

        if scenario_id == "existing-output":
            inputs = valid_candidate_inputs()
            inputs["base_panel"] = base_panel
            materialized = materialize_sandbox_candidate(**inputs)
            output = output_root / "existing-output"
            output.mkdir()
            sentinel = output / "sentinel"
            sentinel.write_bytes(b"keep")
            try:
                _publish_candidate(materialized, inputs, output)
            except ContractError as exc:
                return {
                    b"keep": "no_clobber"
                }[sentinel.read_bytes()], exc
            raise AssertionError("candidate publisher clobbered existing output")

        if scenario_id == "reversed-row-order":
            reversed_inputs = deepcopy(self.evaluation_inputs)
            for key in (
                "observations",
                "oracle_documents",
                "diagnoses",
                "proposals",
                "candidates",
            ):
                reversed_inputs[key].reverse()
            second = evaluate_synthetic_study(**reversed_inputs)
            first = deepcopy(self.evaluation)
            for value in (first, second):
                value["evaluation_sha256"] = None
            return {
                True: "deterministic"
            }[first == second], {"candidate_bindings": []}

        if scenario_id == "sealed-holdout-reuse":
            sealed = proposal_inputs_fixture(
                scenario_id="non-identifiable-twin-a"
            )
            attacked = evaluation_inputs_fixture(
                diagnoses=[
                    *deepcopy(self.evaluation_inputs["diagnoses"]),
                    sealed["diagnosis"],
                ]
            )
            try:
                evaluate_synthetic_study(**attacked)
            except SealedHoldoutFailure as exc:
                return "hard_failure", exc
            raise AssertionError("sealed diagnosis entered engine results")

        if scenario_id == "base-panel-package-bytes":
            before = package_zip.read_bytes()
            inputs = valid_candidate_inputs()
            inputs["base_panel"] = base_panel
            materialize_sandbox_candidate(**inputs)
            return {
                True: "preserved"
            }[before == package_zip.read_bytes()], (
                before,
                package_zip.read_bytes(),
            )

        if scenario_id == "ad-testing-output-bytes":
            before = canonical_json_bytes(self.evaluation_inputs["exercise"])
            evaluate_synthetic_study(**deepcopy(self.evaluation_inputs))
            after = canonical_json_bytes(self.evaluation_inputs["exercise"])
            return {True: "preserved"}[before == after], (before, after)

        if scenario_id == "nonlinear-saturation":
            sensitivity = next(
                row
                for row in self.evaluation["measures"][
                    "sensitivity_by_frozen_assumption"
                ]
                if row["scenario_family_id"] == scenario_id
            )
            return {
                (scenario_id, "abstain"): "sensitivity_reported"
            }[(sensitivity["scenario_family_id"], scenario["actual_action"])], [
                scenario,
                sensitivity,
            ]

        if scenario_id == "delayed-censored-outcomes":
            rows = _normalize_scenario(
                scenario_id=scenario_id,
                platform_name="linkedin",
                study_manifest=manifest,
                registry=registry,
            )
            states = {row["completeness"]["metric_state"] for row in rows}
            return {
                (True, "abstain"): "ineligible_until_final"
            }[(
                bool({"missing", "suppressed"} & states),
                scenario["actual_action"],
            )], [scenario, *rows]

        if scenario_id == "zero-inflated-value":
            rows = _normalize_scenario(
                scenario_id=scenario_id,
                platform_name="linkedin",
                study_manifest=manifest,
                registry=registry,
            )
            values = [
                event["count"]
                for row in rows
                for event in row["outcome_events"]
                if event["count"] is not None
            ]
            return {
                (True, True, "abstain"): "honest_abstention"
            }[(0 in values, any(value > 0 for value in values), scenario[
                "actual_action"
            ])], [scenario, *rows]

        if scenario_id == "production-library-snapshot":
            before = _tree_bytes(production_library)
            validate_synthetic_evaluation(deepcopy(self.evaluation))
            return {
                True: "preserved"
            }[before == _tree_bytes(production_library)], (
                before,
                _tree_bytes(production_library),
            )

        raise AssertionError(f"no observed probe for {scenario_id}")

    def test_exact_29_case_matrix_and_candidate_states(self):
        self.assertEqual(29, len(_EXPECTED_GOLDENS))
        self.assertEqual(29, len({row[0] for row in _EXPECTED_GOLDENS}))
        base_panel = candidate_base_panel_fixture()
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            package = build_audience_package(
                json.loads((V2_FIXTURES / "approved-brief.json").read_bytes()),
                json.loads((V2_FIXTURES / "approved-panel.json").read_bytes()),
                root / "approved-v2-package",
            )
            production_library = root / "production-audience-library"
            registration = register_package(
                package.package_zip_path,
                library_root=production_library,
            )
            self.assertEqual("registered", registration["status"])
            (production_library / "active.json").write_bytes(
                canonical_json_bytes(
                    {
                        "panel_id": registration["panel"]["panel_id"],
                        "version": registration["panel"]["version"],
                        "package_manifest_sha256": registration["panel"][
                            "package_manifest_sha256"
                        ],
                    }
                )
            )
            self.assertTrue((production_library / "index.json").is_file())
            self.assertTrue((production_library / "active.json").is_file())

            rows = []
            for index, (scenario_id, expected, _) in enumerate(
                _EXPECTED_GOLDENS,
                start=1,
            ):
                with self.subTest(scenario_id=scenario_id):
                    output_root = (
                        root / f"golden-{index:02d}-{scenario_id}"
                    )
                    output_root.mkdir()
                    base_before = canonical_json_bytes(base_panel)
                    library_before = _tree_bytes(production_library)
                    actual_result, _observation = self._observe_golden_path(
                        scenario_id,
                        output_root=output_root,
                        base_panel=base_panel,
                        production_library=production_library,
                        package_zip=package.package_zip_path,
                    )
                    candidate_bundles = _observe_closed_candidate_bundles(
                        output_root
                    )
                    rows.append(
                        {
                            "scenario_id": scenario_id,
                            "expected_result": expected,
                            "actual_result": actual_result,
                            "candidate_created": bool(candidate_bundles),
                            "base_panel_unchanged": (
                                base_before == canonical_json_bytes(base_panel)
                            ),
                            "active_panel_mutated": (
                                library_before
                                != _tree_bytes(production_library)
                            ),
                        }
                    )

            _assert_observed_rows(rows)

    def test_observed_matrix_rejects_forced_result_and_mutation(self):
        rows = [
            {
                "scenario_id": scenario_id,
                "actual_result": result,
                "candidate_created": candidate,
                "base_panel_unchanged": True,
                "active_panel_mutated": False,
            }
            for scenario_id, result, candidate in _EXPECTED_GOLDENS
        ]
        forced_result = deepcopy(rows)
        forced_result[0]["actual_result"] = "forced-wrong"
        with self.assertRaisesRegex(AssertionError, "observed"):
            _assert_observed_rows(forced_result)

        forced_candidate = deepcopy(rows)
        forced_candidate[0]["candidate_created"] = True
        with self.assertRaisesRegex(AssertionError, "candidate state"):
            _assert_observed_rows(forced_candidate)

        with tempfile.TemporaryDirectory() as raw:
            production = Path(raw)
            (production / "index.json").write_bytes(b"registered-index\n")
            (production / "active.json").write_bytes(b"registered-active\n")
            before = _tree_bytes(production)
            (production / "active.json").write_bytes(b"mutated-active\n")
            forced_mutation = deepcopy(rows)
            forced_mutation[-1]["active_panel_mutated"] = (
                before != _tree_bytes(production)
            )
            with self.assertRaisesRegex(
                AssertionError,
                "production audience library",
            ):
                _assert_observed_rows(forced_mutation)

    def test_candidate_state_requires_complete_observed_bundle_files(self):
        with tempfile.TemporaryDirectory() as raw:
            output_root = Path(raw) / "isolated-golden-output"
            output_root.mkdir()
            self.assertEqual(
                (),
                _observe_closed_candidate_bundles(output_root),
            )

            stray = output_root / "candidate-audience-panel.json"
            stray.write_bytes(canonical_json_bytes({"candidate": "incomplete"}))
            with self.assertRaisesRegex(
                AssertionError,
                "without a bundle manifest",
            ):
                _observe_closed_candidate_bundles(output_root)
            stray.unlink()

            inputs = valid_candidate_inputs()
            materialized = materialize_sandbox_candidate(**inputs)
            bundle = _publish_candidate(
                materialized,
                inputs,
                output_root / "candidate-bundle",
            )
            self.assertEqual(
                (bundle,),
                _observe_closed_candidate_bundles(output_root),
            )

            (bundle / "candidate-audience-panel.json").unlink()
            with self.assertRaisesRegex(
                AssertionError,
                "inventory is incomplete",
            ):
                _observe_closed_candidate_bundles(output_root)

    def test_phase_receipt_dag_and_full_exercise_cross_product(self):
        receipts = self.evaluation_inputs["phase_receipts"]
        self.assertEqual(
            [
                "open_input",
                "engine_result",
                "candidate_seal",
                "sealed_reveal",
                "exercise",
            ],
            [row["phase"] for row in receipts],
        )
        previous = None
        for sequence, receipt in enumerate(receipts):
            self.assertEqual(sequence, receipt["sequence"])
            self.assertEqual(previous, receipt["previous_phase_receipt_sha256"])
            candidate = deepcopy(receipt)
            supplied = candidate.pop("phase_receipt_sha256")
            candidate["phase_receipt_sha256"] = None
            self.assertEqual(sha256_json(candidate), supplied)
            previous = supplied
        manifest = self.exercise_inputs["study_manifest"]
        exercise = self.evaluation_inputs["exercise"]
        expected = {
            (
                family["scenario_id"],
                repetition,
                panel["exercise_panel_ref"],
            )
            for family in manifest["scenario_families"]
            for repetition in range(family["repetitions"])
            for panel in exercise["panel_bindings"]
        }
        actual = {
            (
                row["scenario_id"],
                row["repetition"],
                row["exercise_panel_ref"],
            )
            for row in exercise["run_results"]
        }
        self.assertEqual(expected, actual)
        roster_sizes = {
            row["exercise_panel_ref"]: len(row["members"])
            for row in exercise["panel_rosters"]
        }
        self.assertEqual(
            sum(4 * roster_sizes[row["exercise_panel_ref"]] for row in exercise["run_results"]),
            len(exercise["panelist_jobs"]),
        )

    def test_generator_owned_inventory_and_coverage_matrix_are_exact(self):
        matrix_path = FIXTURES / "coverage-matrix.json"
        matrix = json.loads(matrix_path.read_bytes())
        inventory = sorted(
            path.relative_to(FIXTURES).as_posix()
            for path in FIXTURES.rglob("*")
            if path.is_file() and path != matrix_path
        )
        self.assertEqual(inventory, matrix["fixture_inventory"])
        self.assertEqual(
            {row[0].replace("_", "-") for row in _EXPECTED_GOLDENS},
            {row["behavior_id"] for row in matrix["rows"]},
        )
        evidence = execute_coverage_matrix(
            matrix,
            fixture_root=FIXTURES,
            evaluation=self.evaluation,
        )
        generalized = {
            row["behavior_id"]
            for row in matrix["rows"]
            if row["coverage_status"] == "dgp_generalized"
        }
        self.assertEqual({"zero-inflated-value"}, generalized)
        self.assertEqual(
            {
                "nonlinear_saturation",
                "delayed_censored",
                "heavy_tailed",
                "zero_inflated",
            },
            {row["dgp_class"] for row in evidence},
        )

    @unittest.skipUnless(
        os.environ.get("RUN_EXPERIMENTAL_CALIBRATION_PROVIDER_TESTS") == "1",
        "set RUN_EXPERIMENTAL_CALIBRATION_PROVIDER_TESTS=1 for real providers",
    )
    def test_real_provider_runs_completed_entrypoints_without_oracle_authority(self):
        if platform.system() == "Darwin":
            self.assertTrue(Path("/usr/bin/sandbox-exec").is_file())
        elif platform.system() == "Linux":
            self.assertTrue(Path("/usr/bin/bwrap").is_file())
        else:
            self.skipTest("real provider is unavailable on this platform")

        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw).resolve()
            oracle = root / "oracle"
            oracle.mkdir()
            (oracle / "hidden-oracle.json").write_bytes(
                canonical_json_bytes({"private": "denied"})
            )
            oracle_before = _tree_digest(oracle)

            diagnosis_inputs = diagnosis_inputs_fixture()
            diagnosis_arguments: dict[str, object] = {}
            for key, value in diagnosis_inputs.items():
                if key in {"diagnosis_id", "diagnosed_at"}:
                    diagnosis_arguments[key] = value
                    continue
                argument_name = (
                    "creative_attribute_registry"
                    if key == "attribute_registry"
                    else key
                )
                path = root / f"diagnose-{key}.json"
                path.write_bytes(canonical_json_bytes(value))
                diagnosis_arguments[argument_name] = path
            diagnosis_result = run_engine_in_private_stage(
                engine_entrypoint="diagnose",
                validated_arguments=diagnosis_arguments,
                oracle_denied_roots=[oracle],
                output_dir=root / "diagnosis-output",
            )
            self.assertTrue(Path(diagnosis_result["output_path"]).is_file())

            proposal_inputs = proposal_inputs_fixture()
            proposal_arguments: dict[str, object] = {}
            for key, value in proposal_inputs.items():
                if key in {"proposal_id", "proposed_at"}:
                    proposal_arguments[key] = value
                    continue
                argument_name = (
                    "creative_attribute_registry"
                    if key == "attribute_registry"
                    else key
                )
                path = root / f"propose-{key}.json"
                path.write_bytes(canonical_json_bytes(value))
                proposal_arguments[argument_name] = path
            proposal_result = run_engine_in_private_stage(
                engine_entrypoint="propose",
                validated_arguments=proposal_arguments,
                oracle_denied_roots=[oracle],
                output_dir=root / "proposal-output",
            )
            self.assertTrue(Path(proposal_result["output_path"]).is_file())

            materialize_inputs = valid_candidate_inputs()
            materialize_arguments: dict[str, object] = {}
            for key, value in materialize_inputs.items():
                if key in {"candidate_id", "candidate_version", "created_at"}:
                    materialize_arguments[key] = value
                    continue
                argument_name = (
                    "attribute_registry"
                    if key == "attribute_registry"
                    else key
                )
                path = root / f"materialize-{key}.json"
                path.write_bytes(canonical_json_bytes(value))
                materialize_arguments[argument_name] = path
            materialize_result = run_engine_in_private_stage(
                engine_entrypoint="materialize",
                validated_arguments=materialize_arguments,
                oracle_denied_roots=[oracle],
                output_dir=root / "materialize-output",
            )
            self.assertTrue(Path(materialize_result["output_path"]).is_dir())

            exercise_inputs = exercise_inputs_fixture()
            public_root = root / "public"
            public_root.mkdir()
            for partition in ("open", "sealed"):
                shutil.copytree(FIXTURES / partition, public_root / partition)
            exercise_arguments: dict[str, object] = {
                "exercise_id": exercise_inputs["exercise_id"],
                "exercised_at": exercise_inputs["exercised_at"],
                "public_scenarios_root": public_root,
            }
            for key in (
                "study_manifest",
                "creative_attribute_registry",
                "base_panel",
                "candidate_bindings_and_panels",
            ):
                path = root / f"exercise-{key}.json"
                path.write_bytes(canonical_json_bytes(exercise_inputs[key]))
                exercise_arguments[key] = path
            exercise_result = run_engine_in_private_stage(
                engine_entrypoint="exercise",
                validated_arguments=exercise_arguments,
                oracle_denied_roots=[oracle],
                output_dir=root / "exercise-output",
            )
            self.assertTrue(Path(exercise_result["output_path"]).is_file())
            self.assertEqual(
                oracle_before,
                _tree_digest(oracle),
            )


if __name__ == "__main__":
    unittest.main()
