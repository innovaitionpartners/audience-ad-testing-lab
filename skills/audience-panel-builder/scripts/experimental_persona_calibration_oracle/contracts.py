"""Closed private-stage contracts.

Only this package validates hidden simulation truth and its resulting scorecard.
"""

from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
import math
import re

from audience_panel_builder.common import (
    ContractError,
    require_array,
    require_enum,
    require_identifier,
    require_object,
    require_string,
    require_string_array,
    require_timestamp,
    sha256_json,
)
from audience_panel_builder.population.experimental_calibration.contracts import (
    ALLOWED_PERSONA_FIELDS,
    STUDY_MANIFEST_VERSION,
    validate_study_manifest,
)


ORACLE_VERSION = "synthetic-persona-behavior-oracle-v1"
EVALUATION_VERSION = "synthetic-persona-behavior-evaluation-v1"
EVALUATION_PHASE_RECEIPT_VERSION = (
    "synthetic-persona-behavior-evaluation-phase-receipt-v1"
)
_DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")


def _copy(value: object, path: str = "$") -> object:
    if isinstance(value, Mapping):
        copied: dict[str, object] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise ContractError(f"{path} object keys must be strings")
            copied[key] = _copy(item, f"{path}.{key}")
        return copied
    if isinstance(value, list):
        return [_copy(item, f"{path}[{index}]") for index, item in enumerate(value)]
    if isinstance(value, float) and not math.isfinite(value):
        raise ContractError(f"{path} must not contain NaN or infinity")
    if value is None or isinstance(value, (str, bool, int, float)):
        return deepcopy(value)
    raise ContractError(f"{path} must be JSON-shaped")


def _object(value: object, keys: set[str], path: str) -> dict[str, object]:
    return dict(require_object(_copy(value, path), keys, path))


def _digest(value: object, path: str) -> str:
    text = require_string(value, path)
    if not _DIGEST.fullmatch(text):
        raise ContractError(f"{path} must be a sha256: prefixed digest")
    return text


def _integer(value: object, path: str, *, minimum: int = 0) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise ContractError(f"{path} must be an integer >= {minimum}")
    return value


def _number(
    value: object,
    path: str,
    *,
    minimum: float | None = None,
    maximum: float | None = None,
) -> float:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(float(value))
    ):
        raise ContractError(f"{path} must be a finite number")
    result = float(value)
    if minimum is not None and result < minimum:
        raise ContractError(f"{path} must be >= {minimum}")
    if maximum is not None and result > maximum:
        raise ContractError(f"{path} must be <= {maximum}")
    return result


def _boolean(value: object, path: str) -> bool:
    if type(value) is not bool:
        raise ContractError(f"{path} must be boolean")
    return value


def _nullable_boolean(value: object, path: str) -> bool | None:
    if value is None:
        return None
    return _boolean(value, path)


def _self_hash(document: dict[str, object], field: str, path: str) -> dict[str, object]:
    supplied = _digest(document[field], f"{path}.{field}")
    candidate = deepcopy(document)
    candidate[field] = None
    if supplied != sha256_json(candidate):
        raise ContractError(f"{path}.{field} does not match canonical content")
    return document


def _behavioral_miss(value: object, path: str) -> dict[str, object] | None:
    if value is None:
        return None
    document = _object(value, {"target_persona_id", "target_field"}, path)
    require_identifier(document["target_persona_id"], f"{path}.target_persona_id")
    require_enum(
        document["target_field"],
        set(ALLOWED_PERSONA_FIELDS),
        f"{path}.target_field",
    )
    return document


def _operation(value: object, path: str) -> dict[str, object] | None:
    if value is None:
        return None
    document = _object(
        value,
        {
            "operation_type",
            "target_persona_id",
            "target_field",
            "expected_value",
            "value_direction_rule",
        },
        path,
    )
    require_enum(
        document["operation_type"],
        {"profile_snapshot_update"},
        f"{path}.operation_type",
    )
    require_identifier(document["target_persona_id"], f"{path}.target_persona_id")
    require_enum(
        document["target_field"],
        set(ALLOWED_PERSONA_FIELDS),
        f"{path}.target_field",
    )
    require_string_array(
        document["expected_value"],
        f"{path}.expected_value",
        nonempty=True,
    )
    require_enum(
        document["value_direction_rule"],
        {"exact_array_equality"},
        f"{path}.value_direction_rule",
    )
    return document


def validate_oracle(payload: object) -> dict[str, object]:
    keys = {
        "schema_version", "oracle_id", "study_manifest_binding", "scenario_id",
        "repetition", "physical_truth", "epistemic_truth", "failure_mechanism",
        "counterfactual_values", "oracle_sha256",
    }
    document = _object(payload, keys, "oracle")
    require_enum(document["schema_version"], {ORACLE_VERSION}, "oracle.schema_version")
    require_identifier(document["oracle_id"], "oracle.oracle_id")
    binding = _object(document["study_manifest_binding"], {"study_id", "study_manifest_sha256"}, "oracle.study_manifest_binding")
    require_identifier(binding["study_id"], "oracle.study_manifest_binding.study_id")
    _digest(binding["study_manifest_sha256"], "oracle.study_manifest_binding.study_manifest_sha256")
    require_identifier(document["scenario_id"], "oracle.scenario_id")
    _integer(document["repetition"], "oracle.repetition")
    physical = _object(
        document["physical_truth"],
        {"true_behavioral_miss", "safe_action_set", "true_operation"},
        "oracle.physical_truth",
    )
    miss = _behavioral_miss(
        physical["true_behavioral_miss"],
        "oracle.physical_truth.true_behavioral_miss",
    )
    actions = require_array(
        physical["safe_action_set"],
        "oracle.physical_truth.safe_action_set",
        nonempty=True,
    )
    for index, action in enumerate(actions):
        require_enum(
            action,
            {"no_change", "profile_snapshot_update"},
            f"oracle.physical_truth.safe_action_set[{index}]",
        )
    if len(actions) != len(set(actions)):
        raise ContractError("oracle.physical_truth.safe_action_set must be unique")
    true_operation = _operation(
        physical["true_operation"],
        "oracle.physical_truth.true_operation",
    )
    epistemic = _object(
        document["epistemic_truth"],
        {
            "identification_status",
            "expected_engine_action",
            "expected_operation",
        },
        "oracle.epistemic_truth",
    )
    identification = require_enum(
        epistemic["identification_status"],
        {"identified", "no_miss", "non_identifiable"},
        "oracle.epistemic_truth.identification_status",
    )
    expected_action = require_enum(
        epistemic["expected_engine_action"],
        {"abstain", "no_change", "profile_snapshot_update"},
        "oracle.epistemic_truth.expected_engine_action",
    )
    expected_operation = _operation(
        epistemic["expected_operation"],
        "oracle.epistemic_truth.expected_operation",
    )
    mechanism = _object(document["failure_mechanism"], {"kind"}, "oracle.failure_mechanism")
    require_identifier(mechanism["kind"], "oracle.failure_mechanism.kind")
    values = _object(document["counterfactual_values"], {"effect"}, "oracle.counterfactual_values")
    if isinstance(values["effect"], bool) or not isinstance(values["effect"], (int, float)) or not math.isfinite(values["effect"]):
        raise ContractError("oracle.counterfactual_values.effect must be a finite number")
    if identification == "no_miss":
        if (
            miss is not None
            or true_operation is not None
            or actions != ["no_change"]
            or expected_action != "no_change"
            or expected_operation is not None
            or values["effect"] != 0.0
        ):
            raise ContractError("oracle no_miss truth must contain only null operations")
    elif identification == "identified":
        if (
            miss is None
            or true_operation is None
            or actions != ["profile_snapshot_update"]
            or expected_action != "profile_snapshot_update"
            or expected_operation != true_operation
        ):
            raise ContractError(
                "oracle identified truth must bind the exact expected operation"
            )
    elif expected_action != "abstain" or expected_operation is not None:
        raise ContractError(
            "oracle non_identifiable truth must require epistemic abstention"
        )
    if miss is None and true_operation is not None:
        raise ContractError("oracle true operation requires a true behavioral miss")
    if miss is not None and true_operation is not None:
        if (
            miss["target_persona_id"] != true_operation["target_persona_id"]
            or miss["target_field"] != true_operation["target_field"]
        ):
            raise ContractError(
                "oracle true miss and operation must target the same persona field"
            )
    return _self_hash(document, "oracle_sha256", "oracle")


def validate_evaluation_phase_receipt(payload: object) -> dict[str, object]:
    keys = {
        "schema_version",
        "phase_id",
        "phase",
        "sequence",
        "partition",
        "study_manifest_sha256",
        "scenario_bindings",
        "record_bindings",
        "provider_bindings",
        "previous_phase_receipt_sha256",
        "phase_receipt_sha256",
    }
    document = _object(payload, keys, "phase_receipt")
    require_enum(
        document["schema_version"],
        {EVALUATION_PHASE_RECEIPT_VERSION},
        "phase_receipt.schema_version",
    )
    require_identifier(document["phase_id"], "phase_receipt.phase_id")
    require_enum(
        document["phase"],
        {
            "open_input",
            "engine_result",
            "candidate_seal",
            "sealed_reveal",
            "exercise",
        },
        "phase_receipt.phase",
    )
    _integer(document["sequence"], "phase_receipt.sequence")
    require_enum(
        document["partition"],
        {"open", "sealed", "both"},
        "phase_receipt.partition",
    )
    _digest(
        document["study_manifest_sha256"],
        "phase_receipt.study_manifest_sha256",
    )
    scenarios = require_array(
        document["scenario_bindings"],
        "phase_receipt.scenario_bindings",
    )
    scenario_ids: set[str] = set()
    for index, raw in enumerate(scenarios):
        path = f"phase_receipt.scenario_bindings[{index}]"
        row = _object(
            raw,
            {
                "scenario_id",
                "repetition",
                "partition",
                "scenario_manifest_sha256",
                "observations_sha256",
            },
            path,
        )
        scenario_id = require_identifier(row["scenario_id"], f"{path}.scenario_id")
        repetition = _integer(row["repetition"], f"{path}.repetition")
        scenario_key = f"{scenario_id}:{repetition}"
        if scenario_key in scenario_ids:
            raise ContractError(
                "phase receipt scenario repetitions must be unique"
            )
        scenario_ids.add(scenario_key)
        require_enum(row["partition"], {"open", "sealed"}, f"{path}.partition")
        _digest(
            row["scenario_manifest_sha256"],
            f"{path}.scenario_manifest_sha256",
        )
        _digest(row["observations_sha256"], f"{path}.observations_sha256")
    records = require_array(
        document["record_bindings"],
        "phase_receipt.record_bindings",
    )
    record_keys: set[tuple[str, str]] = set()
    for index, raw in enumerate(records):
        path = f"phase_receipt.record_bindings[{index}]"
        row = _object(
            raw,
            {
                "kind",
                "record_id",
                "sha256",
                "scenario_id",
                "repetition",
                "partition",
            },
            path,
        )
        kind = require_enum(
            row["kind"],
            {
                "observation_set",
                "diagnosis",
                "proposal",
                "candidate",
                "oracle",
                "exercise",
            },
            f"{path}.kind",
        )
        record_id = require_identifier(row["record_id"], f"{path}.record_id")
        if (kind, record_id) in record_keys:
            raise ContractError("phase receipt record bindings must be unique")
        record_keys.add((kind, record_id))
        _digest(row["sha256"], f"{path}.sha256")
        partition = require_enum(
            row["partition"],
            {"open", "sealed", "both"},
            f"{path}.partition",
        )
        if kind == "exercise":
            if (
                row["scenario_id"] is not None
                or row["repetition"] is not None
                or partition != "both"
            ):
                raise ContractError(
                    "exercise phase record must bind the complete study"
                )
        else:
            require_identifier(row["scenario_id"], f"{path}.scenario_id")
            _integer(row["repetition"], f"{path}.repetition")
            if partition == "both":
                raise ContractError(
                    "scenario record partition must be open or sealed"
                )
    providers = require_array(
        document["provider_bindings"],
        "phase_receipt.provider_bindings",
        nonempty=True,
    )
    provider_ids: set[str] = set()
    for index, raw in enumerate(providers):
        path = f"phase_receipt.provider_bindings[{index}]"
        row = _object(
            raw,
            {
                "provider_id",
                "arguments_sha256",
                "admitted_input_tree_sha256",
                "first_party_source_closure_sha256",
                "external_dependency_closure_sha256",
                "runtime_binding_sha256",
                "output_sha256",
            },
            path,
        )
        provider_id = require_identifier(row["provider_id"], f"{path}.provider_id")
        if provider_id in provider_ids:
            raise ContractError("phase receipt provider IDs must be unique")
        provider_ids.add(provider_id)
        for field in (
            "arguments_sha256",
            "admitted_input_tree_sha256",
            "first_party_source_closure_sha256",
            "external_dependency_closure_sha256",
            "runtime_binding_sha256",
            "output_sha256",
        ):
            _digest(row[field], f"{path}.{field}")
    previous = document["previous_phase_receipt_sha256"]
    if previous is not None:
        _digest(previous, "phase_receipt.previous_phase_receipt_sha256")
    return _self_hash(document, "phase_receipt_sha256", "phase_receipt")


def _validate_rate_measure(value: object, path: str) -> dict[str, object]:
    row = _object(
        value,
        {
            "repetitions",
            "point_estimate",
            "monte_carlo_standard_error",
        },
        path,
    )
    _integer(row["repetitions"], f"{path}.repetitions")
    _number(row["point_estimate"], f"{path}.point_estimate", minimum=0, maximum=1)
    _number(
        row["monte_carlo_standard_error"],
        f"{path}.monte_carlo_standard_error",
        minimum=0,
    )
    return row


def _validate_report_projection(value: object) -> dict[str, object]:
    path = "evaluation.report_projection"
    row = _object(
        value,
        {
            "visible_result_state",
            "existing_persona_behavior",
            "proposed_hypothesis",
            "candidate_created",
            "exact_persona_diff",
            "associations",
            "supporting_evidence",
            "contrary_evidence",
            "alternative_explanations",
            "measurement_context",
            "abstentions",
            "failures",
            "limits",
            "technical_bindings",
        },
        path,
    )
    require_enum(
        row["visible_result_state"],
        {
            "No change recommended",
            "Unable to determine",
            "Behavioral update proposed",
            "Sandbox candidate created",
            "Evidence invalid",
        },
        f"{path}.visible_result_state",
    )
    _boolean(row["candidate_created"], f"{path}.candidate_created")
    for field_name in ("existing_persona_behavior", "proposed_hypothesis"):
        fields = require_array(row[field_name], f"{path}.{field_name}")
        seen: set[str] = set()
        for index, raw in enumerate(fields):
            item_path = f"{path}.{field_name}[{index}]"
            item = _object(raw, {"field", "value"}, item_path)
            field = require_enum(
                item["field"],
                set(ALLOWED_PERSONA_FIELDS),
                f"{item_path}.field",
            )
            if field in seen:
                raise ContractError(f"{path}.{field_name} fields must be unique")
            seen.add(field)
            value = item["value"]
            if not isinstance(value, (str, list)):
                raise ContractError(f"{item_path}.value must be string or array")
            if isinstance(value, list):
                require_string_array(value, f"{item_path}.value", nonempty=True)
    for field_name in (
        "exact_persona_diff",
        "associations",
        "supporting_evidence",
        "contrary_evidence",
        "abstentions",
        "failures",
        "limits",
    ):
        require_string_array(row[field_name], f"{path}.{field_name}")
    causes = require_array(
        row["alternative_explanations"],
        f"{path}.alternative_explanations",
    )
    cause_names: set[str] = set()
    for index, raw in enumerate(causes):
        item_path = f"{path}.alternative_explanations[{index}]"
        item = _object(raw, {"cause", "status"}, item_path)
        cause = require_identifier(item["cause"], f"{item_path}.cause")
        if cause in cause_names:
            raise ContractError("report alternative explanations must be unique")
        cause_names.add(cause)
        require_enum(
            item["status"],
            {"cleared", "not_cleared", "unknown"},
            f"{item_path}.status",
        )
    contexts = require_array(
        row["measurement_context"],
        f"{path}.measurement_context",
    )
    for index, raw in enumerate(contexts):
        item_path = f"{path}.measurement_context[{index}]"
        item = _object(
            raw,
            {
                "platform",
                "metric",
                "denominator",
                "attribution",
                "maturity",
            },
            item_path,
        )
        for field in item:
            require_string(item[field], f"{item_path}.{field}")
    technical = require_array(
        row["technical_bindings"],
        f"{path}.technical_bindings",
        nonempty=True,
    )
    for index, raw in enumerate(technical):
        item_path = f"{path}.technical_bindings[{index}]"
        item = _object(raw, {"label", "sha256"}, item_path)
        require_string(item["label"], f"{item_path}.label")
        _digest(item["sha256"], f"{item_path}.sha256")
    return row


def validate_synthetic_evaluation(payload: object) -> dict[str, object]:
    keys = {
        "schema_version",
        "evaluation_id",
        "evaluated_at",
        "status",
        "evidence_origin",
        "real_world_validation_status",
        "study_manifest",
        "study_manifest_binding",
        "evidence_bindings",
        "phase_receipt_chain",
        "scenario_family_results",
        "measures",
        "production_authority",
        "report_projection",
        "limitations",
        "evaluation_sha256",
    }
    document = _object(payload, keys, "evaluation")
    require_enum(
        document["schema_version"],
        {EVALUATION_VERSION},
        "evaluation.schema_version",
    )
    require_identifier(document["evaluation_id"], "evaluation.evaluation_id")
    require_timestamp(document["evaluated_at"], "evaluation.evaluated_at")
    require_enum(
        document["status"],
        {"experimental_only"},
        "evaluation.status",
    )
    require_enum(
        document["evidence_origin"],
        {"synthetic_fixture_only"},
        "evaluation.evidence_origin",
    )
    require_enum(
        document["real_world_validation_status"],
        {"not_evaluated"},
        "evaluation.real_world_validation_status",
    )
    manifest = validate_study_manifest(document["study_manifest"])
    binding = _object(
        document["study_manifest_binding"],
        {"study_id", "study_manifest_sha256"},
        "evaluation.study_manifest_binding",
    )
    require_identifier(
        binding["study_id"], "evaluation.study_manifest_binding.study_id"
    )
    _digest(
        binding["study_manifest_sha256"],
        "evaluation.study_manifest_binding.study_manifest_sha256",
    )
    if binding != {
        "study_id": manifest["study_id"],
        "study_manifest_sha256": manifest["manifest_sha256"],
    }:
        raise ContractError(
            "evaluation.study_manifest_binding must match study_manifest"
        )

    evidence = _object(
        document["evidence_bindings"],
        {
            "observation_sets",
            "exercise",
            "oracle_sha256s",
            "diagnosis_sha256s",
            "proposal_sha256s",
            "candidate_binding_sha256s",
        },
        "evaluation.evidence_bindings",
    )
    observation_sets = require_array(
        evidence["observation_sets"],
        "evaluation.evidence_bindings.observation_sets",
        nonempty=True,
    )
    observation_keys: set[tuple[str, int]] = set()
    for index, raw in enumerate(observation_sets):
        path = f"evaluation.evidence_bindings.observation_sets[{index}]"
        row = _object(
            raw,
            {
                "scenario_id",
                "repetition",
                "observation_count",
                "observations_sha256",
            },
            path,
        )
        key = (
            require_identifier(row["scenario_id"], f"{path}.scenario_id"),
            _integer(row["repetition"], f"{path}.repetition"),
        )
        if key in observation_keys:
            raise ContractError("evaluation observation sets must be unique")
        observation_keys.add(key)
        _integer(
            row["observation_count"],
            f"{path}.observation_count",
            minimum=1,
        )
        _digest(row["observations_sha256"], f"{path}.observations_sha256")
    exercise = _object(
        evidence["exercise"],
        {"exercise_id", "exercise_sha256", "run_result_count"},
        "evaluation.evidence_bindings.exercise",
    )
    require_identifier(
        exercise["exercise_id"],
        "evaluation.evidence_bindings.exercise.exercise_id",
    )
    _digest(
        exercise["exercise_sha256"],
        "evaluation.evidence_bindings.exercise.exercise_sha256",
    )
    _integer(
        exercise["run_result_count"],
        "evaluation.evidence_bindings.exercise.run_result_count",
        minimum=1,
    )
    for field in (
        "oracle_sha256s",
        "diagnosis_sha256s",
        "proposal_sha256s",
        "candidate_binding_sha256s",
    ):
        values = require_array(
            evidence[field], f"evaluation.evidence_bindings.{field}"
        )
        checked = [
            _digest(value, f"evaluation.evidence_bindings.{field}[{index}]")
            for index, value in enumerate(values)
        ]
        if checked != sorted(set(checked)):
            raise ContractError(
                f"evaluation.evidence_bindings.{field} must be sorted and unique"
            )

    phases = require_array(
        document["phase_receipt_chain"],
        "evaluation.phase_receipt_chain",
        nonempty=True,
    )
    checked_phases = [
        validate_evaluation_phase_receipt(row) for row in phases
    ]
    expected_phase_names = [
        "open_input",
        "engine_result",
        "candidate_seal",
        "sealed_reveal",
        "exercise",
    ]
    if [row["phase"] for row in checked_phases] != expected_phase_names:
        raise ContractError("evaluation phase receipt chain is incomplete")
    previous = None
    for index, row in enumerate(checked_phases):
        if (
            row["sequence"] != index
            or row["previous_phase_receipt_sha256"] != previous
        ):
            raise ContractError("evaluation phase receipt chain link is stale")
        previous = row["phase_receipt_sha256"]

    families = {
        str(row["scenario_id"]): row for row in manifest["scenario_families"]
    }
    rows = require_array(
        document["scenario_family_results"],
        "evaluation.scenario_family_results",
        nonempty=True,
    )
    seen: set[str] = set()
    scenario_results_flat: list[dict[str, object]] = []
    family_robustness: dict[str, dict[str, object]] = {}
    for index, raw in enumerate(rows):
        path = f"evaluation.scenario_family_results[{index}]"
        member = _object(
            raw,
            {
                "scenario_family_id",
                "dgp_id",
                "dgp_version",
                "partition",
                "parameter_set_sha256",
                "scenario_results",
                "robustness",
                "result_sha256",
            },
            path,
        )
        family_id = require_identifier(
            member["scenario_family_id"], f"{path}.scenario_family_id"
        )
        if family_id in seen or family_id not in families:
            raise ContractError(
                "evaluation scenario families are missing or duplicated"
            )
        seen.add(family_id)
        expected_family = families[family_id]
        if (
            member["dgp_id"] != expected_family["dgp_id"]
            or member["dgp_version"] != expected_family["dgp_version"]
            or member["partition"] != expected_family["partition"]
            or member["parameter_set_sha256"]
            != expected_family["parameters"]["parameters_sha256"]
        ):
            raise ContractError("evaluation scenario family binding is stale")
        _digest(member["parameter_set_sha256"], f"{path}.parameter_set_sha256")
        scenarios = require_array(
            member["scenario_results"],
            f"{path}.scenario_results",
            nonempty=True,
        )
        repetitions: set[int] = set()
        for scenario_index, raw_scenario in enumerate(scenarios):
            scenario_path = f"{path}.scenario_results[{scenario_index}]"
            scenario = _object(
                raw_scenario,
                {
                    "scenario_id",
                    "repetition",
                    "epistemic_family_id",
                    "oracle_binding",
                    "engine_binding",
                    "expected_action",
                    "actual_action",
                    "result",
                    "exercise_leaves",
                    "measures",
                    "failure_details",
                    "result_sha256",
                },
                scenario_path,
            )
            if (
                require_identifier(
                    scenario["scenario_id"], f"{scenario_path}.scenario_id"
                )
                != family_id
            ):
                raise ContractError("evaluation scenario result family is stale")
            repetition = _integer(
                scenario["repetition"], f"{scenario_path}.repetition"
            )
            if repetition in repetitions:
                raise ContractError(
                    "evaluation scenario repetitions must be unique"
                )
            repetitions.add(repetition)
            require_identifier(
                scenario["epistemic_family_id"],
                f"{scenario_path}.epistemic_family_id",
            )
            oracle = _object(
                scenario["oracle_binding"],
                {"oracle_id", "oracle_sha256"},
                f"{scenario_path}.oracle_binding",
            )
            require_identifier(
                oracle["oracle_id"], f"{scenario_path}.oracle_binding.oracle_id"
            )
            _digest(
                oracle["oracle_sha256"],
                f"{scenario_path}.oracle_binding.oracle_sha256",
            )
            engine = _object(
                scenario["engine_binding"],
                {
                    "diagnosis_id",
                    "diagnosis_sha256",
                    "proposal_id",
                    "proposal_sha256",
                    "candidate_bindings",
                },
                f"{scenario_path}.engine_binding",
            )
            for identity, digest in (
                ("diagnosis_id", "diagnosis_sha256"),
                ("proposal_id", "proposal_sha256"),
            ):
                if engine[identity] is None:
                    if engine[digest] is not None:
                        raise ContractError(
                            f"{scenario_path}.engine_binding {identity} is incomplete"
                        )
                else:
                    require_identifier(
                        engine[identity],
                        f"{scenario_path}.engine_binding.{identity}",
                    )
                    _digest(
                        engine[digest],
                        f"{scenario_path}.engine_binding.{digest}",
                    )
            candidate_rows = require_array(
                engine["candidate_bindings"],
                f"{scenario_path}.engine_binding.candidate_bindings",
            )
            candidate_ids: set[str] = set()
            for candidate_index, raw_candidate in enumerate(candidate_rows):
                candidate_path = (
                    f"{scenario_path}.engine_binding.candidate_bindings"
                    f"[{candidate_index}]"
                )
                candidate = _object(
                    raw_candidate,
                    {
                        "candidate_id",
                        "candidate_binding_sha256",
                        "candidate_build_correct",
                    },
                    candidate_path,
                )
                candidate_id = require_identifier(
                    candidate["candidate_id"],
                    f"{candidate_path}.candidate_id",
                )
                if candidate_id in candidate_ids:
                    raise ContractError(
                        "evaluation candidate bindings must be unique"
                    )
                candidate_ids.add(candidate_id)
                _digest(
                    candidate["candidate_binding_sha256"],
                    f"{candidate_path}.candidate_binding_sha256",
                )
                _boolean(
                    candidate["candidate_build_correct"],
                    f"{candidate_path}.candidate_build_correct",
                )
            require_enum(
                scenario["expected_action"],
                {"abstain", "no_change", "profile_snapshot_update"},
                f"{scenario_path}.expected_action",
            )
            require_enum(
                scenario["actual_action"],
                {"abstain", "no_change", "profile_snapshot_update"},
                f"{scenario_path}.actual_action",
            )
            require_enum(
                scenario["result"],
                {
                    "correct_proposal",
                    "correct_no_change",
                    "correct_abstention",
                    "false_proposal",
                    "missed_proposal",
                    "incorrect_proposal",
                    "incorrect_certainty",
                },
                f"{scenario_path}.result",
            )
            leaves = require_array(
                scenario["exercise_leaves"],
                f"{scenario_path}.exercise_leaves",
                nonempty=True,
            )
            leaf_refs: set[str] = set()
            for leaf_index, raw_leaf in enumerate(leaves):
                leaf_path = f"{scenario_path}.exercise_leaves[{leaf_index}]"
                leaf = _object(
                    raw_leaf,
                    {
                        "exercise_panel_ref",
                        "panel_kind",
                        "candidate_id",
                        "panel_sha256",
                        "exercise_result_sha256",
                        "runtime_source_input_binding_sha256",
                        "numerical_replay",
                    },
                    leaf_path,
                )
                ref = require_identifier(
                    leaf["exercise_panel_ref"],
                    f"{leaf_path}.exercise_panel_ref",
                )
                if ref in leaf_refs:
                    raise ContractError(
                        "evaluation exercise leaves must be unique"
                    )
                leaf_refs.add(ref)
                require_enum(
                    leaf["panel_kind"],
                    {"base", "candidate"},
                    f"{leaf_path}.panel_kind",
                )
                if leaf["panel_kind"] == "base":
                    if leaf["candidate_id"] is not None:
                        raise ContractError(
                            "base evaluation leaf cannot bind a candidate"
                        )
                else:
                    require_identifier(
                        leaf["candidate_id"], f"{leaf_path}.candidate_id"
                    )
                for field in (
                    "panel_sha256",
                    "exercise_result_sha256",
                    "runtime_source_input_binding_sha256",
                ):
                    _digest(leaf[field], f"{leaf_path}.{field}")
                require_enum(
                    leaf["numerical_replay"],
                    {"replayed", "dependency_deferred"},
                    f"{leaf_path}.numerical_replay",
                )
            measure = _object(
                scenario["measures"],
                {
                    "false_proposal",
                    "missed_proposal",
                    "correct_no_change",
                    "correct_abstention",
                    "incorrect_certainty",
                    "target_persona_correct",
                    "changed_fields_correct",
                    "candidate_build_correct",
                    "forbidden_diff_count",
                    "direction_error",
                    "value_error",
                    "uncertainty_coverage",
                    "deterministic_replay",
                    "oracle_isolation",
                    "non_mutation",
                },
                f"{scenario_path}.measures",
            )
            for field in (
                "false_proposal",
                "missed_proposal",
                "correct_no_change",
                "correct_abstention",
                "incorrect_certainty",
                "direction_error",
                "value_error",
                "deterministic_replay",
                "oracle_isolation",
                "non_mutation",
            ):
                _boolean(measure[field], f"{scenario_path}.measures.{field}")
            for field in (
                "target_persona_correct",
                "changed_fields_correct",
                "candidate_build_correct",
            ):
                _nullable_boolean(
                    measure[field], f"{scenario_path}.measures.{field}"
                )
            _integer(
                measure["forbidden_diff_count"],
                f"{scenario_path}.measures.forbidden_diff_count",
            )
            if measure["uncertainty_coverage"] is not None:
                _number(
                    measure["uncertainty_coverage"],
                    f"{scenario_path}.measures.uncertainty_coverage",
                    minimum=0,
                    maximum=1,
                )
            require_string_array(
                scenario["failure_details"],
                f"{scenario_path}.failure_details",
            )
            _self_hash(scenario, "result_sha256", scenario_path)
            scenario_results_flat.append(scenario)
        if repetitions != set(range(int(expected_family["repetitions"]))):
            raise ContractError(
                "evaluation scenario results must preserve every repetition"
            )
        robustness = _object(
            member["robustness"],
            {
                "repetitions",
                "correct_count",
                "failure_count",
                "point_estimate",
                "monte_carlo_standard_error",
            },
            f"{path}.robustness",
        )
        count = _integer(
            robustness["repetitions"], f"{path}.robustness.repetitions", minimum=1
        )
        correct_count = _integer(
            robustness["correct_count"],
            f"{path}.robustness.correct_count",
        )
        failure_count = _integer(
            robustness["failure_count"],
            f"{path}.robustness.failure_count",
        )
        expected_correct = sum(
            1
            for row in scenarios
            if row["result"]
            in {"correct_proposal", "correct_no_change", "correct_abstention"}
        )
        if (
            count != len(scenarios)
            or correct_count != expected_correct
            or failure_count != len(scenarios) - expected_correct
            or robustness["point_estimate"] != expected_correct / len(scenarios)
        ):
            raise ContractError(
                "evaluation family robustness does not match derived results"
            )
        _number(
            robustness["monte_carlo_standard_error"],
            f"{path}.robustness.monte_carlo_standard_error",
            minimum=0,
        )
        family_robustness[family_id] = robustness
        _self_hash(member, "result_sha256", path)
    if seen != set(families):
        raise ContractError(
            "evaluation scenario results must preserve every manifest family"
        )

    measures = _object(
        document["measures"],
        {
            "false_proposal_rate_under_null",
            "correct_abstention_rate",
            "robustness_by_dgp_family",
            "sensitivity_by_frozen_assumption",
            "deterministic_replay",
            "oracle_isolation",
            "sealed_holdout_integrity",
            "zero_activation_mutation",
            "dependency_complete_numerical_replay",
            "failure_count",
        },
        "evaluation.measures",
    )
    false_rate = _validate_rate_measure(
        measures["false_proposal_rate_under_null"],
        "evaluation.measures.false_proposal_rate_under_null",
    )
    abstention_rate = _validate_rate_measure(
        measures["correct_abstention_rate"],
        "evaluation.measures.correct_abstention_rate",
    )
    null_rows = [
        row
        for row in scenario_results_flat
        if row["expected_action"] == "no_change"
    ]
    abstention_rows = [
        row
        for row in scenario_results_flat
        if row["expected_action"] == "abstain"
    ]
    expected_false_rate = (
        sum(row["measures"]["false_proposal"] for row in null_rows)
        / len(null_rows)
        if null_rows
        else 0.0
    )
    expected_abstention_rate = (
        sum(row["measures"]["correct_abstention"] for row in abstention_rows)
        / len(abstention_rows)
        if abstention_rows
        else 0.0
    )
    if (
        false_rate["repetitions"] != len(null_rows)
        or false_rate["point_estimate"] != expected_false_rate
        or abstention_rate["repetitions"] != len(abstention_rows)
        or abstention_rate["point_estimate"] != expected_abstention_rate
    ):
        raise ContractError(
            "evaluation stochastic measures do not match derived scenario rows"
        )
    robustness_rows = require_array(
        measures["robustness_by_dgp_family"],
        "evaluation.measures.robustness_by_dgp_family",
        nonempty=True,
    )
    robustness_ids: set[str] = set()
    for index, raw in enumerate(robustness_rows):
        path = f"evaluation.measures.robustness_by_dgp_family[{index}]"
        row = _object(
            raw,
            {
                "scenario_family_id",
                "dgp_id",
                "repetitions",
                "correct_count",
                "failure_count",
                "point_estimate",
                "monte_carlo_standard_error",
            },
            path,
        )
        family_id = require_identifier(
            row["scenario_family_id"], f"{path}.scenario_family_id"
        )
        if family_id in robustness_ids or family_id not in families:
            raise ContractError(
                "evaluation robustness rows are missing or duplicated"
            )
        robustness_ids.add(family_id)
        expected = family_robustness[family_id]
        if row["dgp_id"] != families[family_id]["dgp_id"] or any(
            row[field] != expected[field]
            for field in (
                "repetitions",
                "correct_count",
                "failure_count",
                "point_estimate",
                "monte_carlo_standard_error",
            )
        ):
            raise ContractError("evaluation robustness row is not derived")
    if robustness_ids != set(families):
        raise ContractError("evaluation robustness must preserve every family")
    sensitivity = require_array(
        measures["sensitivity_by_frozen_assumption"],
        "evaluation.measures.sensitivity_by_frozen_assumption",
        nonempty=True,
    )
    sensitivity_ids: set[str] = set()
    for index, raw in enumerate(sensitivity):
        path = f"evaluation.measures.sensitivity_by_frozen_assumption[{index}]"
        row = _object(
            raw,
            {
                "scenario_family_id",
                "parameter_set_sha256",
                "parameter_values_sha256",
            },
            path,
        )
        family_id = require_identifier(
            row["scenario_family_id"], f"{path}.scenario_family_id"
        )
        if family_id in sensitivity_ids or family_id not in families:
            raise ContractError(
                "evaluation sensitivity rows are missing or duplicated"
            )
        sensitivity_ids.add(family_id)
        _digest(row["parameter_set_sha256"], f"{path}.parameter_set_sha256")
        _digest(
            row["parameter_values_sha256"], f"{path}.parameter_values_sha256"
        )
        if (
            row["parameter_set_sha256"]
            != families[family_id]["parameters"]["parameters_sha256"]
        ):
            raise ContractError(
                "evaluation sensitivity parameter binding is stale"
            )
    if sensitivity_ids != set(families):
        raise ContractError("evaluation sensitivity must preserve every family")
    for field in (
        "deterministic_replay",
        "oracle_isolation",
        "sealed_holdout_integrity",
        "zero_activation_mutation",
        "dependency_complete_numerical_replay",
    ):
        _boolean(measures[field], f"evaluation.measures.{field}")
    expected_failures = sum(
        1
        for row in scenario_results_flat
        if row["result"]
        not in {"correct_proposal", "correct_no_change", "correct_abstention"}
    )
    if (
        _integer(
            measures["failure_count"], "evaluation.measures.failure_count"
        )
        != expected_failures
    ):
        raise ContractError("evaluation failure_count is not derived")

    authority = _object(
        document["production_authority"],
        {
            "package_created",
            "resolution_created",
            "registration_permitted",
            "activation_permitted",
            "active_panel_mutation_permitted",
        },
        "evaluation.production_authority",
    )
    if any(value is not False for value in authority.values()):
        raise ContractError("evaluation cannot grant production authority")
    _validate_report_projection(document["report_projection"])
    require_string_array(
        document["limitations"], "evaluation.limitations", nonempty=True
    )
    return _self_hash(document, "evaluation_sha256", "evaluation")
