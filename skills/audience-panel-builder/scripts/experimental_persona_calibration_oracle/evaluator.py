"""Hidden-truth grading for the experimental persona-behavior sandbox."""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from copy import deepcopy
import math

from audience_panel_builder.common import (
    ContractError,
    canonical_json_bytes,
    sha256_json,
)
from audience_panel_builder.population.experimental_calibration.contracts import (
    _validate_trusted_generator_observation_v1,
    validate_diagnosis,
    validate_experimental_proposal,
    validate_outcome_observation,
    validate_sandbox_candidate_binding,
    validate_study_manifest,
    validate_synthetic_exercise,
)

from .contracts import (
    EVALUATION_VERSION,
    validate_evaluation_phase_receipt,
    validate_oracle,
    validate_synthetic_evaluation,
)


class OracleIsolationFailure(ContractError):
    """The engine-visible graph contains hidden-answer material."""


class SealedHoldoutFailure(ContractError):
    """The sealed-input chronology is incomplete or out of order."""


_PHASES = (
    "open_input",
    "engine_result",
    "candidate_seal",
    "sealed_reveal",
    "exercise",
)
_CORRECT_RESULTS = {
    "correct_proposal",
    "correct_no_change",
    "correct_abstention",
}
_PRIVATE_TOKENS = {
    "hidden_oracle",
    "oracle_sha256",
    "true_behavioral_miss",
    "safe_action_set",
    "physical_truth",
    "epistemic_truth",
    "counterfactual_values",
}


def _finite_number(value: object, path: str) -> float:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(float(value))
    ):
        raise ContractError(f"{path} must be a finite number")
    return float(value)


def _private_tokens(value: object, path: str = "$") -> list[str]:
    failures: list[str] = []
    if isinstance(value, Mapping):
        for key, item in value.items():
            normalized = str(key).lower().replace("-", "_")
            if normalized in _PRIVATE_TOKENS:
                failures.append(f"{path}.{key}")
            failures.extend(_private_tokens(item, f"{path}.{key}"))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            failures.extend(_private_tokens(item, f"{path}[{index}]"))
    return failures


def _validate_observation(
    payload: object,
) -> dict[str, object]:
    if not isinstance(payload, Mapping):
        raise ContractError("evaluation observations must contain JSON objects")
    try:
        return validate_outcome_observation(payload)
    except ContractError as public_error:
        try:
            return _validate_trusted_generator_observation_v1(payload)
        except ContractError:
            raise public_error


def _validate_observation_envelopes(
    observations: Sequence[dict[str, object]],
    manifest: Mapping[str, object],
) -> tuple[list[dict[str, object]], dict[tuple[str, int], dict[str, object]]]:
    families = {
        str(row["scenario_id"]): int(row["repetitions"])
        for row in manifest["scenario_families"]
    }
    checked: list[dict[str, object]] = []
    by_key: dict[tuple[str, int], dict[str, object]] = {}
    for index, raw in enumerate(observations):
        if not isinstance(raw, Mapping) or set(raw) != {
            "scenario_id",
            "repetition",
            "observations",
            "observations_sha256",
        }:
            raise ContractError(
                f"observations[{index}] must be a closed scenario envelope"
            )
        scenario_id = raw["scenario_id"]
        repetition = raw["repetition"]
        if (
            not isinstance(scenario_id, str)
            or scenario_id not in families
            or isinstance(repetition, bool)
            or not isinstance(repetition, int)
            or repetition not in range(families[scenario_id])
        ):
            raise ContractError("observation envelope is outside the study matrix")
        key = (scenario_id, repetition)
        if key in by_key:
            raise ContractError("observation envelopes must be unique")
        rows = raw["observations"]
        if not isinstance(rows, list) or not rows:
            raise ContractError("observation envelope must contain observations")
        validated = [_validate_observation(row) for row in rows]
        if _private_tokens(validated):
            raise OracleIsolationFailure(
                "engine-visible observations contain hidden-oracle material"
            )
        supplied = raw["observations_sha256"]
        expected = sha256_json(validated)
        if supplied != expected:
            raise ContractError("observation envelope hash is stale")
        document = {
            "scenario_id": scenario_id,
            "repetition": repetition,
            "observations": validated,
            "observations_sha256": expected,
        }
        checked.append(document)
        by_key[key] = document
    expected_keys = {
        (scenario_id, repetition)
        for scenario_id, repetitions in families.items()
        for repetition in range(repetitions)
    }
    if set(by_key) != expected_keys:
        raise ContractError("observations must cover every study repetition")
    checked.sort(key=lambda row: (row["scenario_id"], row["repetition"]))
    return checked, by_key


def _scenario_design_map(
    exercise: Mapping[str, object],
) -> dict[str, str]:
    return {
        str(row["experiment_design_sha256"]): str(row["scenario_id"])
        for row in exercise["public_scenario_bindings"]
    }


def _documents_by_scenario(
    *,
    diagnoses: Sequence[dict[str, object]],
    proposals: Sequence[dict[str, object]],
    exercise: Mapping[str, object],
) -> tuple[
    dict[str, dict[str, object]],
    dict[str, dict[str, object]],
]:
    design_to_scenario = _scenario_design_map(exercise)
    diagnosis_by_scenario: dict[str, dict[str, object]] = {}
    diagnosis_scenario_by_hash: dict[str, str] = {}
    for raw in diagnoses:
        document = validate_diagnosis(raw)
        design_hashes = document["frozen_analysis_bindings"][
            "experiment_design_sha256"
        ]
        scenarios = {
            design_to_scenario.get(str(digest)) for digest in design_hashes
        }
        if None in scenarios or len(scenarios) != 1:
            raise ContractError(
                "diagnosis does not bind exactly one exercised scenario"
            )
        scenario_id = str(next(iter(scenarios)))
        if scenario_id in diagnosis_by_scenario:
            raise ContractError("each scenario may have at most one diagnosis")
        diagnosis_by_scenario[scenario_id] = document
        diagnosis_scenario_by_hash[
            str(document["diagnosis_sha256"])
        ] = scenario_id

    proposal_by_scenario: dict[str, dict[str, object]] = {}
    for raw in proposals:
        document = validate_experimental_proposal(raw)
        diagnosis_hash = str(document["diagnosis"]["diagnosis_sha256"])
        scenario_id = diagnosis_scenario_by_hash.get(diagnosis_hash)
        if scenario_id is None:
            raise ContractError("proposal does not bind an admitted diagnosis")
        if scenario_id in proposal_by_scenario:
            raise ContractError("each scenario may have at most one proposal")
        diagnosis = diagnosis_by_scenario[scenario_id]
        if (
            document["diagnosis"]["diagnosis_id"] != diagnosis["diagnosis_id"]
            or document["diagnosis"]["decision"] != diagnosis["decision"]
        ):
            raise ContractError("proposal diagnosis binding is stale")
        proposal_by_scenario[scenario_id] = document
    return diagnosis_by_scenario, proposal_by_scenario


def _validate_candidates(
    candidates: Sequence[dict[str, object]],
    proposals: Mapping[str, dict[str, object]],
    exercise: Mapping[str, object],
) -> tuple[
    list[dict[str, object]],
    dict[str, list[dict[str, object]]],
]:
    proposal_scenario_by_hash = {
        str(proposal["proposal_sha256"]): scenario_id
        for scenario_id, proposal in proposals.items()
    }
    checked: list[dict[str, object]] = []
    by_scenario: dict[str, list[dict[str, object]]] = defaultdict(list)
    ids: set[str] = set()
    for raw in candidates:
        candidate = validate_sandbox_candidate_binding(raw)
        candidate_id = str(candidate["candidate_id"])
        if candidate_id in ids:
            raise ContractError("candidate IDs must be unique")
        ids.add(candidate_id)
        if (
            candidate["forbidden_diff_check"]["passed"] is not True
            or candidate["forbidden_diff_check"]["forbidden_paths"]
        ):
            raise ContractError("candidate contains a forbidden diff")
        scenario_id = proposal_scenario_by_hash.get(
            str(candidate["proposal_binding"]["proposal_sha256"])
        )
        if scenario_id is None:
            raise ContractError("candidate does not bind an admitted proposal")
        proposal = proposals[scenario_id]
        if (
            candidate["proposal_binding"]["proposal_id"]
            != proposal["proposal_id"]
        ):
            raise ContractError("candidate proposal identity is stale")
        checked.append(candidate)
        by_scenario[scenario_id].append(candidate)

    exercise_candidates = {
        str(row["candidate_id"]): row
        for row in exercise["panel_bindings"]
        if row["panel_kind"] == "candidate"
    }
    if set(exercise_candidates) != ids:
        raise ContractError(
            "candidate list and exercised candidate set must match exactly"
        )
    for candidate in checked:
        binding = exercise_candidates[str(candidate["candidate_id"])]
        if (
            binding["candidate_binding_sha256"]
            != candidate["candidate_binding_sha256"]
            or binding["proposal_sha256"]
            != candidate["proposal_binding"]["proposal_sha256"]
            or binding["panel_sha256"]
            != candidate["candidate_panel_binding"]["panel_sha256"]
        ):
            raise ContractError("candidate does not byte-bind its exercise panel")
    checked.sort(key=lambda row: str(row["candidate_id"]))
    for values in by_scenario.values():
        values.sort(key=lambda row: str(row["candidate_id"]))
    return checked, dict(by_scenario)


def _validate_phase_chain(
    phase_receipts: Sequence[dict[str, object]],
    *,
    manifest: Mapping[str, object],
    observations: Mapping[tuple[str, int], dict[str, object]],
    exercise: Mapping[str, object],
    diagnoses: Mapping[str, dict[str, object]],
    proposals: Mapping[str, dict[str, object]],
    candidates: Sequence[dict[str, object]],
    oracles: Mapping[tuple[str, int], dict[str, object]],
) -> tuple[list[dict[str, object]], bool]:
    if len(phase_receipts) != len(_PHASES):
        raise ContractError("phase receipt chain must contain exactly five phases")
    checked = [
        validate_evaluation_phase_receipt(receipt)
        for receipt in phase_receipts
    ]
    if [row["phase"] for row in checked] != list(_PHASES):
        raise ContractError("phase receipt chain is reordered")
    previous = None
    for index, row in enumerate(checked):
        if row["sequence"] != index:
            raise ContractError("phase receipt sequence is not contiguous")
        if row["previous_phase_receipt_sha256"] != previous:
            raise ContractError("phase receipt chain link is stale")
        if (
            row["study_manifest_sha256"]
            != manifest["manifest_sha256"]
        ):
            raise ContractError("phase receipt study binding is stale")
        previous = row["phase_receipt_sha256"]

    family_by_id = {
        str(row["scenario_id"]): row for row in manifest["scenario_families"]
    }
    open_ids = {
        scenario_id
        for scenario_id, row in family_by_id.items()
        if row["partition"] == "open"
    }
    sealed_ids = set(family_by_id) - open_ids
    diagnosis_scenarios = set(diagnoses)
    proposal_scenarios = set(proposals)
    proposal_scenario_by_hash = {
        str(proposal["proposal_sha256"]): scenario_id
        for scenario_id, proposal in proposals.items()
    }
    candidate_scenarios = {
        proposal_scenario_by_hash[
            str(candidate["proposal_binding"]["proposal_sha256"])
        ]
        for candidate in candidates
    }
    if not diagnosis_scenarios <= open_ids:
        raise SealedHoldoutFailure(
            "a diagnosis was derived from a sealed scenario before reveal"
        )
    if not proposal_scenarios <= open_ids:
        raise SealedHoldoutFailure(
            "a proposal was derived from a sealed scenario before reveal"
        )
    if not candidate_scenarios <= open_ids:
        raise SealedHoldoutFailure(
            "a candidate was derived from a sealed scenario before reveal"
        )

    def scenario_ids(receipt: Mapping[str, object]) -> set[str]:
        return {
            str(row["scenario_id"]) for row in receipt["scenario_bindings"]
        }

    if not scenario_ids(checked[0]) <= open_ids:
        raise SealedHoldoutFailure(
            "sealed scenarios appeared before the sealed reveal"
        )
    if not scenario_ids(checked[1]) <= open_ids:
        raise SealedHoldoutFailure(
            "engine results consumed a sealed scenario"
        )
    if not scenario_ids(checked[2]) <= open_ids:
        raise SealedHoldoutFailure(
            "candidate sealing consumed a sealed scenario"
        )
    if scenario_ids(checked[3]) != sealed_ids:
        raise SealedHoldoutFailure(
            "sealed reveal must bind every sealed scenario exactly once"
        )
    if scenario_ids(checked[4]) != set(family_by_id):
        raise SealedHoldoutFailure(
            "exercise phase must bind every revealed scenario"
        )

    expected_scenario_bindings = {
        (scenario_id, repetition): {
            "scenario_id": scenario_id,
            "repetition": repetition,
            "partition": family_by_id[scenario_id]["partition"],
            "scenario_manifest_sha256": next(
                row["scenario_manifest_sha256"]
                for row in exercise["public_scenario_bindings"]
                if row["scenario_id"] == scenario_id
            ),
            "observations_sha256": observations[(scenario_id, repetition)][
                "observations_sha256"
            ],
        }
        for scenario_id in family_by_id
        for repetition in range(
            int(family_by_id[scenario_id]["repetitions"])
        )
    }
    for receipt in checked:
        for binding in receipt["scenario_bindings"]:
            key = (str(binding["scenario_id"]), int(binding["repetition"]))
            if binding != expected_scenario_bindings[key]:
                raise ContractError("phase receipt scenario binding is stale")

    expected_records = {
        "open_input": {
            (
                "observation_set",
                f"{scenario_id}-r{repetition}",
                observations[(scenario_id, repetition)]["observations_sha256"],
                scenario_id,
                repetition,
                "open",
            )
            for scenario_id in open_ids
            for repetition in range(
                int(family_by_id[scenario_id]["repetitions"])
            )
        },
        "engine_result": {
            (
                "diagnosis",
                str(row["diagnosis_id"]),
                str(row["diagnosis_sha256"]),
                scenario_id,
                0,
                "open",
            )
            for scenario_id, row in diagnoses.items()
        }
        | {
            (
                "proposal",
                str(row["proposal_id"]),
                str(row["proposal_sha256"]),
                scenario_id,
                0,
                "open",
            )
            for scenario_id, row in proposals.items()
        },
        "candidate_seal": {
            (
                "candidate",
                str(row["candidate_id"]),
                str(row["candidate_binding_sha256"]),
                proposal_scenario_by_hash[
                    str(row["proposal_binding"]["proposal_sha256"])
                ],
                0,
                "open",
            )
            for row in candidates
        },
        "sealed_reveal": {
            (
                "oracle",
                str(oracles[(scenario_id, repetition)]["oracle_id"]),
                str(oracles[(scenario_id, repetition)]["oracle_sha256"]),
                scenario_id,
                repetition,
                "sealed",
            )
            for scenario_id in sealed_ids
            for repetition in range(
                int(family_by_id[scenario_id]["repetitions"])
            )
        }
        | {
            (
                "observation_set",
                f"{scenario_id}-r{repetition}",
                observations[(scenario_id, repetition)]["observations_sha256"],
                scenario_id,
                repetition,
                "sealed",
            )
            for scenario_id in sealed_ids
            for repetition in range(
                int(family_by_id[scenario_id]["repetitions"])
            )
        },
        "exercise": {
            (
                "exercise",
                str(exercise["exercise_id"]),
                str(exercise["exercise_sha256"]),
                None,
                None,
                "both",
            )
        },
    }
    for receipt in checked:
        actual = {
            (
                row["kind"],
                row["record_id"],
                row["sha256"],
                row["scenario_id"],
                row["repetition"],
                row["partition"],
            )
            for row in receipt["record_bindings"]
        }
        if actual != expected_records[receipt["phase"]]:
            raise ContractError(
                f"{receipt['phase']} phase receipt records are stale"
            )
    return checked, True


def _validate_oracles(
    oracle_documents: Sequence[dict[str, object]],
    manifest: Mapping[str, object],
) -> dict[tuple[str, int], dict[str, object]]:
    expected = {
        (str(row["scenario_id"]), repetition)
        for row in manifest["scenario_families"]
        for repetition in range(int(row["repetitions"]))
    }
    by_key: dict[tuple[str, int], dict[str, object]] = {}
    for raw in oracle_documents:
        oracle = validate_oracle(raw)
        if (
            oracle["study_manifest_binding"]["study_id"]
            != manifest["study_id"]
            or oracle["study_manifest_binding"]["study_manifest_sha256"]
            != manifest["manifest_sha256"]
        ):
            raise ContractError("oracle study binding does not match evaluation")
        key = (str(oracle["scenario_id"]), int(oracle["repetition"]))
        if key not in expected or key in by_key:
            raise ContractError("oracle scenario/repetition is missing or duplicated")
        by_key[key] = oracle
    if set(by_key) != expected:
        raise ContractError("oracle documents must cover every study repetition")
    return by_key


def _validate_twin_family(
    *,
    observations: Mapping[tuple[str, int], dict[str, object]],
    oracles: Mapping[tuple[str, int], dict[str, object]],
) -> set[tuple[str, int]]:
    twin_ids = ("non-identifiable-twin-a", "non-identifiable-twin-b")
    repetitions = sorted(
        {
            repetition
            for scenario_id, repetition in observations
            if scenario_id == twin_ids[0]
        }
        & {
            repetition
            for scenario_id, repetition in observations
            if scenario_id == twin_ids[1]
        }
    )
    keys = {
        (scenario_id, repetition)
        for scenario_id in twin_ids
        for repetition in repetitions
    }
    if not keys or not keys <= set(observations) or not keys <= set(oracles):
        return set()
    for repetition in repetitions:
        left = observations[(twin_ids[0], repetition)]["observations"]
        right = observations[(twin_ids[1], repetition)]["observations"]
        if canonical_json_bytes(left) != canonical_json_bytes(right):
            raise ContractError(
                "non-identifiable twins must carry identical visible evidence"
            )
        actions = {
            tuple(
                oracles[(scenario_id, repetition)]["physical_truth"][
                    "safe_action_set"
                ]
            )
            for scenario_id in twin_ids
        }
        if len(actions) != 2:
            raise ContractError(
                "non-identifiable twins must have incompatible hidden safe sets"
            )
    if any(
        oracles[key]["epistemic_truth"]["expected_engine_action"] != "abstain"
        for key in keys
    ):
        raise ContractError(
            "non-identifiable twins must freeze epistemic abstention"
        )
    return keys


def _actual_action(
    diagnosis: Mapping[str, object] | None,
    proposal: Mapping[str, object] | None,
) -> str:
    if proposal is not None:
        return str(proposal["proposal_type"])
    if diagnosis is not None and diagnosis["decision"] == "no_repeatable_miss":
        return "no_change"
    return "abstain"


def _operation_measures(
    *,
    expected: Mapping[str, object] | None,
    proposal: Mapping[str, object] | None,
) -> dict[str, bool | None]:
    if expected is None:
        return {
            "target_persona_correct": None,
            "changed_fields_correct": None,
            "direction_error": False,
            "value_error": False,
        }
    operation = None if proposal is None else proposal.get("operation")
    if not isinstance(operation, Mapping):
        return {
            "target_persona_correct": False,
            "changed_fields_correct": False,
            "direction_error": False,
            "value_error": False,
        }
    target = operation.get("target_persona_id") == expected.get(
        "target_persona_id"
    )
    expected_field = expected.get("target_field")
    changed = operation.get("changed_fields") == [expected_field]
    proposed_after = operation.get("proposed_after")
    expected_value = expected.get("expected_value")
    value_correct = (
        isinstance(proposed_after, Mapping)
        and proposed_after == {expected_field: expected_value}
    )
    expected_direction = "positive"
    direction_error = (
        proposal.get("expected_effect", {}).get("direction")
        != expected_direction
    )
    return {
        "target_persona_correct": target,
        "changed_fields_correct": changed,
        "direction_error": direction_error,
        "value_error": not value_correct,
    }


def _classify_result(
    *,
    expected_action: str,
    actual_action: str,
    exact_operation: bool,
) -> str:
    if expected_action == "profile_snapshot_update":
        if actual_action != "profile_snapshot_update":
            return "missed_proposal"
        return "correct_proposal" if exact_operation else "incorrect_proposal"
    if expected_action == "no_change":
        if actual_action == "no_change":
            return "correct_no_change"
        if actual_action == "profile_snapshot_update":
            return "false_proposal"
        return "incorrect_certainty"
    if actual_action == "abstain":
        return "correct_abstention"
    if actual_action == "profile_snapshot_update":
        return "false_proposal"
    return "incorrect_certainty"


def _uncertainty_coverage(
    *,
    diagnosis: Mapping[str, object] | None,
    true_effect: float,
) -> float | None:
    if diagnosis is None:
        return None
    combined = diagnosis.get("analysis", {}).get("combined")
    if not isinstance(combined, Mapping):
        return None
    lower = _finite_number(combined.get("interval_lower"), "interval_lower")
    upper = _finite_number(combined.get("interval_upper"), "interval_upper")
    return 1.0 if lower <= true_effect <= upper else 0.0


def _numerical_replay_status(
    exercise: Mapping[str, object],
) -> dict[str, str]:
    """Replay exact numerical outputs when pinned dependencies are available."""

    if any(
        row["scoring_and_aggregation"]["maxdiff"].get("message")
        == "contract-only dependency-deferred fixture"
        for row in exercise["run_results"]
    ):
        return {
            str(row["result_sha256"]): "dependency_deferred"
            for row in exercise["run_results"]
        }
    try:
        from audience_panel_builder.population.experimental_calibration.exercise import (
            ExerciseDependencyUnavailable,
            _ad_testing_runtime,
            _scoring_projections,
        )

        runtime = _ad_testing_runtime()
    except (ImportError, ModuleNotFoundError, ExerciseDependencyUnavailable):
        return {
            str(row["result_sha256"]): "dependency_deferred"
            for row in exercise["run_results"]
        }

    jobs_by_run: dict[tuple[str, int, str], list[dict[str, object]]] = defaultdict(
        list
    )
    for job in exercise["panelist_jobs"]:
        jobs_by_run[
            (
                str(job["scenario_id"]),
                int(job["repetition"]),
                str(job["exercise_panel_ref"]),
            )
        ].append(job["job"])
    statuses: dict[str, str] = {}
    for row in exercise["run_results"]:
        key = (
            str(row["scenario_id"]),
            int(row["repetition"]),
            str(row["exercise_panel_ref"]),
        )
        inputs = row["scoring_and_aggregation"]["scoring_inputs"]
        replayed, finalists = _scoring_projections(
            runtime=runtime,
            responses=list(row["responses"]),
            jobs=jobs_by_run[key],
            adapter_outputs=list(row["adapter_outputs"]),
            creative_ids=list(inputs["complete_exposure"]["creative_ids"]),
            seed=int(inputs["complete_exposure"]["seed"]),
        )
        if (
            canonical_json_bytes(replayed)
            != canonical_json_bytes(row["scoring_and_aggregation"])
            or canonical_json_bytes(finalists)
            != canonical_json_bytes(row["finalist_responses"])
        ):
            raise ContractError(
                "dependency-complete exercise numerical replay mismatch"
            )
        statuses[str(row["result_sha256"])] = "replayed"
    return statuses


def _binary_measure(values: Sequence[bool]) -> dict[str, object]:
    repetitions = len(values)
    point = sum(1 for value in values if value) / repetitions if repetitions else 0.0
    mcse = (
        math.sqrt(point * (1.0 - point) / repetitions)
        if repetitions
        else 0.0
    )
    return {
        "repetitions": repetitions,
        "point_estimate": point,
        "monte_carlo_standard_error": mcse,
    }


def _measurement_context(
    observations: Sequence[Mapping[str, object]],
) -> list[dict[str, object]]:
    rows: set[tuple[str, str, str, str, str]] = set()
    for envelope in observations:
        for observation in envelope["observations"]:
            measurement = observation["measurement_definition"]
            reporting = observation["reporting_context"]
            source = observation["source"]
            denominator = observation["denominators"]
            platform = str(source.get("platform", source.get("platform_id", "unknown")))
            metric = str(
                measurement.get(
                    "metric",
                    measurement.get("metric_id", "registered-outcome"),
                )
            )
            denominator_kind = str(
                denominator.get(
                    "kind",
                    measurement.get("registered_denominator", "explicit"),
                )
            )
            attribution = str(
                measurement.get(
                    "attribution_model",
                    reporting.get("attribution_model", "explicit"),
                )
            )
            maturity = str(
                reporting.get(
                    "maturity",
                    observation.get("completeness", {}).get("status", "explicit"),
                )
            )
            rows.add(
                (platform, metric, denominator_kind, attribution, maturity)
            )
    return [
        {
            "platform": platform,
            "metric": metric,
            "denominator": denominator,
            "attribution": attribution,
            "maturity": maturity,
        }
        for platform, metric, denominator, attribution, maturity in sorted(rows)
    ]


def _visible_result_state(
    *,
    family_results: Sequence[Mapping[str, object]],
    proposals: Sequence[Mapping[str, object]],
    candidates: Sequence[Mapping[str, object]],
) -> str:
    results = {
        str(scenario["result"])
        for family in family_results
        for scenario in family["scenario_results"]
    }
    if any(result not in _CORRECT_RESULTS for result in results):
        return "Evidence invalid"
    if candidates:
        return "Sandbox candidate created"
    if any(
        proposal["proposal_type"] == "profile_snapshot_update"
        for proposal in proposals
    ):
        return "Behavioral update proposed"
    if proposals and all(
        proposal["proposal_type"] == "no_change"
        for proposal in proposals
    ):
        return "No change recommended"
    return "Unable to determine"


def _report_projection(
    *,
    family_results: Sequence[Mapping[str, object]],
    proposals: Sequence[Mapping[str, object]],
    candidates: Sequence[Mapping[str, object]],
    observations: Sequence[Mapping[str, object]],
    exercise: Mapping[str, object],
) -> dict[str, object]:
    update = next(
        (
            proposal
            for proposal in proposals
            if proposal["proposal_type"] == "profile_snapshot_update"
        ),
        None,
    )
    candidate = candidates[0] if candidates else None
    existing: list[dict[str, object]] = []
    proposed: list[dict[str, object]] = []
    exact_diff: list[str] = []
    if candidate is not None:
        operation = candidate["applied_operation"]
        existing = [
            {"field": field, "value": value}
            for field, value in sorted(operation["before"].items())
        ]
        proposed = [
            {"field": field, "value": value}
            for field, value in sorted(operation["proposed_after"].items())
        ]
        exact_diff = list(candidate["allowed_diff"]["changed_paths"])
    abstentions = sorted(
        scenario["scenario_id"]
        for family in family_results
        for scenario in family["scenario_results"]
        if scenario["result"] == "correct_abstention"
    )
    failures = sorted(
        scenario["scenario_id"]
        for family in family_results
        for scenario in family["scenario_results"]
        if scenario["result"] not in _CORRECT_RESULTS
    )
    causes: list[dict[str, str]] = []
    if update is not None:
        causes = [
            {"cause": cause.replace("_", "-"), "status": row["status"]}
            for cause, row in sorted(
                update["alternative_explanations"].items()
            )
        ]
    technical = [
        {"label": "Exercise", "sha256": exercise["exercise_sha256"]},
        *[
            {"label": "Proposal", "sha256": row["proposal_sha256"]}
            for row in proposals
        ],
        *[
            {"label": "Sandbox candidate", "sha256": row["candidate_binding_sha256"]}
            for row in candidates
        ],
    ]
    return {
        "visible_result_state": _visible_result_state(
            family_results=family_results,
            proposals=proposals,
            candidates=candidates,
        ),
        "existing_persona_behavior": existing,
        "proposed_hypothesis": proposed,
        "candidate_created": candidate is not None,
        "exact_persona_diff": exact_diff,
        "associations": [
            "Compatible fictional creative-feature associations are reported as associations only."
        ],
        "supporting_evidence": [
            f"{len(proposals)} sealed experimental proposal document(s)."
        ],
        "contrary_evidence": [
            f"{len(failures)} scenario family failure(s) remain visible."
        ],
        "alternative_explanations": causes,
        "measurement_context": _measurement_context(observations),
        "abstentions": abstentions,
        "failures": failures,
        "limits": [
            "Fictional synthetic fixtures verify mechanics only.",
            "The proposal is not proven to improve real-world outcomes.",
            "This report cannot modify an active panel.",
            "The sandbox candidate cannot be registered or activated.",
            "Real evidence and separate approval are required before any production use.",
        ],
        "technical_bindings": technical,
    }


def evaluate_synthetic_study(
    *,
    study_manifest: dict[str, object],
    observations: Sequence[dict[str, object]],
    exercise: dict[str, object],
    oracle_documents: Sequence[dict[str, object]],
    diagnoses: Sequence[dict[str, object]],
    proposals: Sequence[dict[str, object]],
    candidates: Sequence[dict[str, object]],
    phase_receipts: Sequence[dict[str, object]],
    evaluated_at: str,
) -> dict[str, object]:
    """Join sealed engine results to hidden truth without granting authority."""

    manifest = validate_study_manifest(study_manifest)
    checked_observations, observations_by_key = _validate_observation_envelopes(
        observations, manifest
    )
    checked_exercise = validate_synthetic_exercise(exercise)
    if checked_exercise["study_manifest_binding"] != {
        "study_id": manifest["study_id"],
        "study_manifest_sha256": manifest["manifest_sha256"],
    }:
        raise ContractError("exercise study binding is stale")
    oracles = _validate_oracles(oracle_documents, manifest)
    diagnosis_by_scenario, proposal_by_scenario = _documents_by_scenario(
        diagnoses=diagnoses,
        proposals=proposals,
        exercise=checked_exercise,
    )
    checked_candidates, candidates_by_scenario = _validate_candidates(
        candidates, proposal_by_scenario, checked_exercise
    )
    checked_phases, sealed_holdout_integrity = _validate_phase_chain(
        phase_receipts,
        manifest=manifest,
        observations=observations_by_key,
        exercise=checked_exercise,
        diagnoses=diagnosis_by_scenario,
        proposals=proposal_by_scenario,
        candidates=checked_candidates,
        oracles=oracles,
    )
    twin_keys = _validate_twin_family(
        observations=observations_by_key,
        oracles=oracles,
    )
    numerical_status = _numerical_replay_status(checked_exercise)

    run_results: dict[tuple[str, int], list[dict[str, object]]] = defaultdict(list)
    for row in checked_exercise["run_results"]:
        run_results[(str(row["scenario_id"]), int(row["repetition"]))].append(row)
    panel_count = len(checked_exercise["panel_bindings"])

    family_results: list[dict[str, object]] = []
    null_flags: list[bool] = []
    abstention_flags: list[bool] = []
    all_failure_count = 0
    for family in sorted(
        manifest["scenario_families"], key=lambda row: str(row["scenario_id"])
    ):
        scenario_id = str(family["scenario_id"])
        scenario_rows: list[dict[str, object]] = []
        for repetition in range(int(family["repetitions"])):
            key = (scenario_id, repetition)
            oracle = oracles[key]
            diagnosis = diagnosis_by_scenario.get(scenario_id)
            proposal = proposal_by_scenario.get(scenario_id)
            expected_action = str(
                oracle["epistemic_truth"]["expected_engine_action"]
            )
            epistemic_family_id = (
                "non-identifiable-twins" if key in twin_keys else scenario_id
            )
            if key in twin_keys:
                expected_action = "abstain"
            actual_action = _actual_action(diagnosis, proposal)
            expected_operation = oracle["epistemic_truth"]["expected_operation"]
            operation_measures = _operation_measures(
                expected=expected_operation,
                proposal=proposal,
            )
            exact_operation = (
                expected_operation is not None
                and operation_measures["target_persona_correct"] is True
                and operation_measures["changed_fields_correct"] is True
                and operation_measures["direction_error"] is False
                and operation_measures["value_error"] is False
            )
            false_proposal = (
                actual_action == "profile_snapshot_update"
                and expected_action != "profile_snapshot_update"
            )
            missed_proposal = (
                expected_action == "profile_snapshot_update"
                and actual_action != "profile_snapshot_update"
            )
            correct_abstention = (
                expected_action == "abstain" and actual_action == "abstain"
            )
            correct_no_change = (
                expected_action == "no_change" and actual_action == "no_change"
            )
            incorrect_certainty = (
                expected_action == "abstain" and actual_action != "abstain"
            )
            result = _classify_result(
                expected_action=expected_action,
                actual_action=actual_action,
                exact_operation=exact_operation,
            )

            scenario_candidates = candidates_by_scenario.get(scenario_id, [])
            candidate_build_correct: bool | None = None
            forbidden_diff_count = 0
            candidate_bindings: list[dict[str, object]] = []
            if expected_action == "profile_snapshot_update":
                candidate_build_correct = bool(scenario_candidates)
                for candidate in scenario_candidates:
                    operation = candidate["applied_operation"]
                    expected_field = expected_operation["target_field"]
                    candidate_correct = (
                        operation["target_persona_id"]
                        == expected_operation["target_persona_id"]
                        and operation["changed_fields"] == [expected_field]
                        and operation["proposed_after"]
                        == {expected_field: expected_operation["expected_value"]}
                        and candidate["forbidden_diff_check"]
                        == {"passed": True, "forbidden_paths": []}
                    )
                    candidate_build_correct = (
                        candidate_build_correct and candidate_correct
                    )
                    forbidden_diff_count += len(
                        candidate["forbidden_diff_check"]["forbidden_paths"]
                    )
                    candidate_bindings.append(
                        {
                            "candidate_id": candidate["candidate_id"],
                            "candidate_binding_sha256": candidate[
                                "candidate_binding_sha256"
                            ],
                            "candidate_build_correct": candidate_correct,
                        }
                    )
            leaves: list[dict[str, object]] = []
            rows = sorted(
                run_results[key],
                key=lambda row: str(row["exercise_panel_ref"]),
            )
            if len(rows) != panel_count:
                raise ContractError(
                    "evaluation exercise matrix has a missing panel leaf"
                )
            for exercise_row in rows:
                scoring = exercise_row["scoring_and_aggregation"]
                runtime_binding = {
                    "scenario_manifest_sha256": exercise_row[
                        "scenario_manifest_sha256"
                    ],
                    "experiment_design_sha256": exercise_row[
                        "experiment_design_sha256"
                    ],
                    "admitted_public_files_sha256": exercise_row[
                        "admitted_public_files_sha256"
                    ],
                    "assignment_plan_sha256": exercise_row[
                        "assignment_plan_sha256"
                    ],
                    "capacity_plan_sha256": exercise_row[
                        "capacity_plan_sha256"
                    ],
                    "job_sha256s": exercise_row["job_sha256s"],
                    "adapter_output_sha256s": exercise_row[
                        "adapter_output_sha256s"
                    ],
                    "response_sha256s": exercise_row["response_sha256s"],
                    "scoring_sha256": scoring["scoring_sha256"],
                }
                leaves.append(
                    {
                        "exercise_panel_ref": exercise_row[
                            "exercise_panel_ref"
                        ],
                        "panel_kind": exercise_row["panel_kind"],
                        "candidate_id": exercise_row["candidate_id"],
                        "panel_sha256": next(
                            row["panel_sha256"]
                            for row in checked_exercise["panel_bindings"]
                            if row["exercise_panel_ref"]
                            == exercise_row["exercise_panel_ref"]
                        ),
                        "exercise_result_sha256": exercise_row[
                            "result_sha256"
                        ],
                        "runtime_source_input_binding_sha256": sha256_json(
                            runtime_binding
                        ),
                        "numerical_replay": numerical_status[
                            str(exercise_row["result_sha256"])
                        ],
                    }
                )
            coverage = _uncertainty_coverage(
                diagnosis=diagnosis,
                true_effect=float(oracle["counterfactual_values"]["effect"]),
            )
            failures: list[str] = []
            if result not in _CORRECT_RESULTS:
                failures.append(
                    f"Expected {expected_action}; engine produced {actual_action}."
                )
            if coverage == 0.0:
                failures.append(
                    "The frozen uncertainty interval did not cover hidden truth."
                )
            if any(
                leaf["numerical_replay"] == "dependency_deferred"
                for leaf in leaves
            ):
                failures.append(
                    "Pinned dependency-complete numerical replay remains pending."
                )
            all_failure_count += 1 if result not in _CORRECT_RESULTS else 0
            if expected_action == "no_change":
                null_flags.append(false_proposal)
            if expected_action == "abstain":
                abstention_flags.append(correct_abstention)
            scenario_document: dict[str, object] = {
                "scenario_id": scenario_id,
                "repetition": repetition,
                "epistemic_family_id": epistemic_family_id,
                "oracle_binding": {
                    "oracle_id": oracle["oracle_id"],
                    "oracle_sha256": oracle["oracle_sha256"],
                },
                "engine_binding": {
                    "diagnosis_id": (
                        None if diagnosis is None else diagnosis["diagnosis_id"]
                    ),
                    "diagnosis_sha256": (
                        None
                        if diagnosis is None
                        else diagnosis["diagnosis_sha256"]
                    ),
                    "proposal_id": (
                        None if proposal is None else proposal["proposal_id"]
                    ),
                    "proposal_sha256": (
                        None if proposal is None else proposal["proposal_sha256"]
                    ),
                    "candidate_bindings": candidate_bindings,
                },
                "expected_action": expected_action,
                "actual_action": actual_action,
                "result": result,
                "exercise_leaves": leaves,
                "measures": {
                    "false_proposal": false_proposal,
                    "missed_proposal": missed_proposal,
                    "correct_no_change": correct_no_change,
                    "correct_abstention": correct_abstention,
                    "incorrect_certainty": incorrect_certainty,
                    **operation_measures,
                    "candidate_build_correct": candidate_build_correct,
                    "forbidden_diff_count": forbidden_diff_count,
                    "uncertainty_coverage": coverage,
                    "deterministic_replay": True,
                    "oracle_isolation": True,
                    "non_mutation": True,
                },
                "failure_details": failures,
                "result_sha256": None,
            }
            scenario_document["result_sha256"] = sha256_json(scenario_document)
            scenario_rows.append(scenario_document)
        correct_count = sum(
            1 for row in scenario_rows if row["result"] in _CORRECT_RESULTS
        )
        robustness = {
            "repetitions": len(scenario_rows),
            "correct_count": correct_count,
            "failure_count": len(scenario_rows) - correct_count,
            "point_estimate": correct_count / len(scenario_rows),
            "monte_carlo_standard_error": math.sqrt(
                (correct_count / len(scenario_rows))
                * (1.0 - correct_count / len(scenario_rows))
                / len(scenario_rows)
            ),
        }
        family_document: dict[str, object] = {
            "scenario_family_id": scenario_id,
            "dgp_id": family["dgp_id"],
            "dgp_version": family["dgp_version"],
            "partition": family["partition"],
            "parameter_set_sha256": family["parameters"]["parameters_sha256"],
            "scenario_results": scenario_rows,
            "robustness": robustness,
            "result_sha256": None,
        }
        family_document["result_sha256"] = sha256_json(family_document)
        family_results.append(family_document)

    robustness_rows = [
        {
            "scenario_family_id": row["scenario_family_id"],
            "dgp_id": row["dgp_id"],
            **row["robustness"],
        }
        for row in family_results
    ]
    sensitivity_rows = [
        {
            "scenario_family_id": row["scenario_id"],
            "parameter_set_sha256": row["parameters"]["parameters_sha256"],
            "parameter_values_sha256": sha256_json(
                row["parameters"]["parameter_values"]
            ),
        }
        for row in sorted(
            manifest["scenario_families"],
            key=lambda value: str(value["scenario_id"]),
        )
    ]
    evidence_bindings = {
        "observation_sets": [
            {
                "scenario_id": row["scenario_id"],
                "repetition": row["repetition"],
                "observation_count": len(row["observations"]),
                "observations_sha256": row["observations_sha256"],
            }
            for row in checked_observations
        ],
        "exercise": {
            "exercise_id": checked_exercise["exercise_id"],
            "exercise_sha256": checked_exercise["exercise_sha256"],
            "run_result_count": len(checked_exercise["run_results"]),
        },
        "oracle_sha256s": sorted(
            str(row["oracle_sha256"]) for row in oracles.values()
        ),
        "diagnosis_sha256s": sorted(
            str(row["diagnosis_sha256"])
            for row in diagnosis_by_scenario.values()
        ),
        "proposal_sha256s": sorted(
            str(row["proposal_sha256"])
            for row in proposal_by_scenario.values()
        ),
        "candidate_binding_sha256s": sorted(
            str(row["candidate_binding_sha256"])
            for row in checked_candidates
        ),
    }
    document: dict[str, object] = {
        "schema_version": EVALUATION_VERSION,
        "evaluation_id": f"{manifest['study_id']}-evaluation",
        "evaluated_at": evaluated_at,
        "status": "experimental_only",
        "evidence_origin": "synthetic_fixture_only",
        "real_world_validation_status": "not_evaluated",
        "study_manifest": deepcopy(manifest),
        "study_manifest_binding": {
            "study_id": manifest["study_id"],
            "study_manifest_sha256": manifest["manifest_sha256"],
        },
        "evidence_bindings": evidence_bindings,
        "phase_receipt_chain": checked_phases,
        "scenario_family_results": family_results,
        "measures": {
            "false_proposal_rate_under_null": _binary_measure(null_flags),
            "correct_abstention_rate": _binary_measure(abstention_flags),
            "robustness_by_dgp_family": robustness_rows,
            "sensitivity_by_frozen_assumption": sensitivity_rows,
            "deterministic_replay": True,
            "oracle_isolation": True,
            "sealed_holdout_integrity": sealed_holdout_integrity,
            "zero_activation_mutation": True,
            "dependency_complete_numerical_replay": all(
                status == "replayed" for status in numerical_status.values()
            ),
            "failure_count": all_failure_count,
        },
        "production_authority": deepcopy(
            checked_exercise["production_authority"]
        ),
        "report_projection": _report_projection(
            family_results=family_results,
            proposals=sorted(
                proposal_by_scenario.values(),
                key=lambda row: str(row["proposal_id"]),
            ),
            candidates=checked_candidates,
            observations=checked_observations,
            exercise=checked_exercise,
        ),
        "limitations": [
            "Built and evaluated with fictional synthetic fixtures only.",
            "This output does not validate real-world panel accuracy.",
            "Pinned SciPy numerical replay is required before release completion.",
        ],
        "evaluation_sha256": None,
    }
    document["evaluation_sha256"] = sha256_json(document)
    return validate_synthetic_evaluation(document)


__all__ = [
    "OracleIsolationFailure",
    "SealedHoldoutFailure",
    "evaluate_synthetic_study",
]
