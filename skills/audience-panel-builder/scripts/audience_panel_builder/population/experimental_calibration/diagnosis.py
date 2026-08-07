"""Frozen diagnosis of synthetic persona-behavior evidence.

This module describes associations in fictional aggregate fixtures.  It has no
saved-panel validation, candidate, package, registration, or activation seam.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping, Sequence
from copy import deepcopy
import hashlib
import math
import random

from ...common import (
    ContractError,
    canonical_json_bytes,
    require_identifier,
    require_timestamp,
    sha256_json,
)
from .contracts import (
    ALLOWED_PERSONA_FIELDS,
    DIAGNOSIS_METHOD_VERSION,
    DIAGNOSIS_VERSION,
    SYNTHETIC_SCENARIO_MANIFEST_SHA256,
    validate_creative_attribute_registry,
    validate_diagnosis,
    validate_evidence_library,
    validate_evidence_receipt,
    validate_study_manifest,
)


_CAUSES = (
    "delivery",
    "targeting",
    "timing",
    "offer",
    "landing_page",
    "tracking",
    "attribution",
)
_DESIGN_KEYS = {
    "schema_version",
    "design_id",
    "study_manifest_binding",
    "scenario_binding",
    "creative_attribute_registry_binding",
    "behavioral_hypothesis",
    "analytical_cells",
    "design_sha256",
}
_NUMERATOR_METRICS = {
    ("meta", "finalized-leads"): "lead",
    ("google", "finalized-leads"): "conversions",
    ("linkedin", "finalized-leads"): "total_conversions",
    ("tiktok", "finalized-leads"): "cta_conversions",
}
_SCENARIO_MANIFEST_KEYS = {
    "schema_version",
    "study_manifest_binding",
    "scenario_binding",
    "partition",
    "public_file_bindings",
    "manifest_sha256",
}


def _closed(value: object, keys: set[str], path: str) -> dict[str, object]:
    if not isinstance(value, Mapping):
        raise ContractError(f"{path} must be an object")
    if set(value) != keys:
        unknown = sorted(set(value) - keys)
        missing = sorted(keys - set(value))
        raise ContractError(
            f"{path} has unknown fields {unknown} or missing fields {missing}"
        )
    return deepcopy(dict(value))


def _self_hashed(
    value: object,
    *,
    keys: set[str],
    hash_field: str,
    path: str,
) -> dict[str, object]:
    document = _closed(value, keys, path)
    supplied = document[hash_field]
    if (
        not isinstance(supplied, str)
        or not supplied.startswith("sha256:")
        or len(supplied) != 71
    ):
        raise ContractError(f"{path}.{hash_field} must be a prefixed digest")
    candidate = deepcopy(document)
    candidate[hash_field] = None
    if supplied != sha256_json(candidate):
        raise ContractError(f"{path}.{hash_field} is invalid")
    return document


def _string(value: object, path: str) -> str:
    if not isinstance(value, str) or not value:
        raise ContractError(f"{path} must be a non-empty string")
    return value


def _string_list(value: object, path: str, *, nonempty: bool = False) -> list[str]:
    if not isinstance(value, list) or (nonempty and not value):
        raise ContractError(f"{path} must be a{' non-empty' if nonempty else ''} array")
    result = [_string(item, f"{path}[{index}]") for index, item in enumerate(value)]
    if len(result) != len(set(result)):
        raise ContractError(f"{path} must contain unique values")
    return result


def _validate_scenario_manifest(
    value: object,
    *,
    manifest: Mapping[str, object],
) -> dict[str, object]:
    document = _self_hashed(
        value,
        keys=_SCENARIO_MANIFEST_KEYS,
        hash_field="manifest_sha256",
        path="scenario_manifest",
    )
    if (
        document["schema_version"]
        != "synthetic-persona-behavior-scenario-manifest-v1"
    ):
        raise ContractError("scenario_manifest.schema_version is unsupported")
    study = _closed(
        document["study_manifest_binding"],
        {"study_id", "study_manifest_sha256"},
        "scenario_manifest.study_manifest_binding",
    )
    expected_study = {
        "study_id": manifest["study_id"],
        "study_manifest_sha256": manifest["manifest_sha256"],
    }
    if study != expected_study:
        raise ContractError("scenario manifest study binding is inconsistent")
    scenario = _closed(
        document["scenario_binding"],
        {
            "study_id",
            "study_manifest_sha256",
            "scenario_id",
            "dgp_id",
            "dgp_version",
            "seed",
            "repetitions",
            "parameters_sha256",
        },
        "scenario_manifest.scenario_binding",
    )
    families = {
        str(family["scenario_id"]): family
        for family in manifest["scenario_families"]
    }
    family = families.get(str(scenario["scenario_id"]))
    if family is None:
        raise ContractError("scenario manifest scenario is not registered")
    expected_scenario = {
        **expected_study,
        "scenario_id": family["scenario_id"],
        "dgp_id": family["dgp_id"],
        "dgp_version": family["dgp_version"],
        "seed": family["seed"],
        "repetitions": family["repetitions"],
        "parameters_sha256": family["parameters"]["parameters_sha256"],
    }
    if scenario != expected_scenario or document["partition"] != family["partition"]:
        raise ContractError("scenario manifest binding is inconsistent")
    if (
        document["manifest_sha256"]
        != SYNTHETIC_SCENARIO_MANIFEST_SHA256.get(str(scenario["scenario_id"]))
    ):
        raise ContractError(
            "scenario manifest is not the frozen generator-1.0.0 manifest"
        )
    bindings = document["public_file_bindings"]
    if not isinstance(bindings, list) or not bindings:
        raise ContractError("scenario_manifest.public_file_bindings must be non-empty")
    paths: list[str] = []
    checked_bindings: list[dict[str, object]] = []
    for index, value_binding in enumerate(bindings):
        path = f"scenario_manifest.public_file_bindings[{index}]"
        binding = _closed(
            value_binding,
            {"path", "byte_count", "raw_bytes_sha256"},
            path,
        )
        paths.append(_string(binding["path"], f"{path}.path"))
        if (
            isinstance(binding["byte_count"], bool)
            or not isinstance(binding["byte_count"], int)
            or binding["byte_count"] < 1
        ):
            raise ContractError(f"{path}.byte_count must be a positive integer")
        digest = binding["raw_bytes_sha256"]
        if (
            not isinstance(digest, str)
            or not digest.startswith("sha256:")
            or len(digest) != 71
        ):
            raise ContractError(f"{path}.raw_bytes_sha256 is invalid")
        checked_bindings.append(binding)
    if paths != sorted(paths) or len(paths) != len(set(paths)):
        raise ContractError(
            "scenario_manifest public file bindings must be unique and sorted"
        )
    if "experiment-design.json" not in paths:
        raise ContractError("scenario manifest does not bind an experiment design")
    document["public_file_bindings"] = checked_bindings
    return document


def _validate_design(
    value: object,
    *,
    manifest: Mapping[str, object],
    registry: Mapping[str, object],
    scenario_manifest: Mapping[str, object],
) -> dict[str, object]:
    document = _self_hashed(
        value,
        keys=_DESIGN_KEYS,
        hash_field="design_sha256",
        path="experiment_design",
    )
    if document["schema_version"] != "fictional-randomized-block-design-v1":
        raise ContractError("experiment_design.schema_version is unsupported")
    require_identifier(document["design_id"], "experiment_design.design_id")
    study = _closed(
        document["study_manifest_binding"],
        {"study_id", "study_manifest_sha256"},
        "experiment_design.study_manifest_binding",
    )
    if study != {
        "study_id": manifest["study_id"],
        "study_manifest_sha256": manifest["manifest_sha256"],
    }:
        raise ContractError("experiment design study binding is inconsistent")
    scenario = _closed(
        document["scenario_binding"],
        {
            "study_id",
            "study_manifest_sha256",
            "scenario_id",
            "dgp_id",
            "dgp_version",
            "seed",
            "repetitions",
            "parameters_sha256",
        },
        "experiment_design.scenario_binding",
    )
    families = {
        member["scenario_id"]: member
        for member in manifest["scenario_families"]
        if isinstance(member, Mapping)
    }
    family = families.get(scenario["scenario_id"])
    if family is None:
        raise ContractError("experiment design scenario is not registered")
    expected_scenario = {
        "study_id": manifest["study_id"],
        "study_manifest_sha256": manifest["manifest_sha256"],
        "scenario_id": family["scenario_id"],
        "dgp_id": family["dgp_id"],
        "dgp_version": family["dgp_version"],
        "seed": family["seed"],
        "repetitions": family["repetitions"],
        "parameters_sha256": family["parameters"]["parameters_sha256"],
    }
    if scenario != expected_scenario:
        raise ContractError("experiment design scenario binding is inconsistent")
    if scenario_manifest["scenario_binding"] != scenario:
        raise ContractError(
            "experiment design is not bound by its scenario manifest"
        )
    design_bytes = canonical_json_bytes(document)
    design_bindings = [
        binding
        for binding in scenario_manifest["public_file_bindings"]
        if binding["path"] == "experiment-design.json"
    ]
    if len(design_bindings) != 1:
        raise ContractError(
            "scenario manifest must bind exactly one experiment design"
        )
    design_binding = design_bindings[0]
    if (
        design_binding["byte_count"] != len(design_bytes)
        or design_binding["raw_bytes_sha256"]
        != "sha256:" + hashlib.sha256(design_bytes).hexdigest()
    ):
        raise ContractError(
            "experiment design bytes do not authenticate to the scenario manifest"
        )
    registry_binding = _closed(
        document["creative_attribute_registry_binding"],
        {"registry_id", "registry_version"},
        "experiment_design.creative_attribute_registry_binding",
    )
    if (
        registry_binding["registry_id"] != registry["registry_id"]
        or registry_binding["registry_version"] != "1.0.0"
    ):
        raise ContractError(
            "experiment design creative-attribute registry binding is inconsistent"
        )
    hypothesis = _closed(
        document["behavioral_hypothesis"],
        {
            "hypothesis_id",
            "target_persona_id",
            "target_field",
            "informative_attribute_id",
            "informative_attribute_value",
            "contrast_direction",
            "predeclared",
        },
        "experiment_design.behavioral_hypothesis",
    )
    for field in (
        "hypothesis_id",
        "target_persona_id",
        "informative_attribute_id",
    ):
        require_identifier(
            hypothesis[field],
            f"experiment_design.behavioral_hypothesis.{field}",
        )
    if hypothesis["target_field"] not in ALLOWED_PERSONA_FIELDS:
        raise ContractError(
            "experiment design hypothesis must target one persona behavior field"
        )
    if (
        hypothesis["contrast_direction"]
        != "treatment_minus_reference_positive"
        or hypothesis["informative_attribute_value"] is not True
        or hypothesis["predeclared"] is not True
    ):
        raise ContractError(
            "experiment design hypothesis must be predeclared with the frozen direction"
        )
    cells = document["analytical_cells"]
    if not isinstance(cells, list) or not cells:
        raise ContractError("experiment_design.analytical_cells must be non-empty")
    checked_cells: list[dict[str, object]] = []
    cell_identities: set[tuple[str, str, str, str]] = set()
    for index, raw_cell in enumerate(cells):
        path = f"experiment_design.analytical_cells[{index}]"
        cell = _closed(
            raw_cell,
            {
                "experiment_id",
                "campaign_id",
                "platform",
                "segment_id",
                "objective",
                "placement",
                "attribution",
                "reporting",
                "arms",
                "randomization",
                "estimand",
            },
            path,
        )
        for field in (
            "experiment_id",
            "campaign_id",
            "platform",
            "segment_id",
            "objective",
            "placement",
        ):
            require_identifier(cell[field], f"{path}.{field}")
        if cell["platform"] not in {"meta", "google", "linkedin", "tiktok"}:
            raise ContractError(f"{path}.platform is unsupported")
        identity = (
            str(cell["experiment_id"]),
            str(cell["campaign_id"]),
            str(cell["platform"]),
            str(cell["segment_id"]),
        )
        if identity in cell_identities:
            raise ContractError("experiment design analytical cells are duplicated")
        cell_identities.add(identity)
        attribution = _closed(
            cell["attribution"],
            {"model", "click_window", "view_window", "engaged_view_window"},
            f"{path}.attribution",
        )
        for field, item in attribution.items():
            _string(item, f"{path}.attribution.{field}")
        reporting = _closed(
            cell["reporting"],
            {"currency", "maturity", "report_time_basis", "timezone"},
            f"{path}.reporting",
        )
        if reporting["maturity"] != "finalized":
            raise ContractError(f"{path}.reporting.maturity must be finalized")
        for field, item in reporting.items():
            _string(item, f"{path}.reporting.{field}")
        estimand = _closed(
            cell["estimand"],
            {
                "estimand_id",
                "registered_numerator",
                "registered_numerator_event",
                "registered_denominator",
                "registered_direction",
                "reference_arm_id",
                "treatment_arm_ids",
                "contrast_direction",
            },
            f"{path}.estimand",
        )
        for field in (
            "estimand_id",
            "registered_numerator",
            "registered_denominator",
            "reference_arm_id",
        ):
            require_identifier(estimand[field], f"{path}.estimand.{field}")
        numerator_event = _closed(
            estimand["registered_numerator_event"],
            {
                "metric_id",
                "event_kind",
                "attribution_kind",
                "report_time_basis",
            },
            f"{path}.estimand.registered_numerator_event",
        )
        for field, item in numerator_event.items():
            _string(item, f"{path}.estimand.registered_numerator_event.{field}")
        if estimand["registered_direction"] != "higher_is_better":
            raise ContractError(
                f"{path}.estimand.registered_direction is invalid"
            )
        treatment_ids = _string_list(
            estimand["treatment_arm_ids"],
            f"{path}.estimand.treatment_arm_ids",
            nonempty=True,
        )
        if treatment_ids != sorted(treatment_ids):
            raise ContractError(
                f"{path}.estimand.treatment_arm_ids must be canonically sorted"
            )
        if (
            estimand["contrast_direction"]
            != hypothesis["contrast_direction"]
        ):
            raise ContractError(
                "experiment design estimand does not admit its informative arm"
            )
        arms = cell["arms"]
        if not isinstance(arms, list) or not arms:
            raise ContractError(f"{path}.arms must be non-empty")
        arm_ids: list[str] = []
        reference_ids: list[str] = []
        for arm_index, raw_arm in enumerate(arms):
            arm = _closed(
                raw_arm,
                {"arm_id", "creative_id", "role"},
                f"{path}.arms[{arm_index}]",
            )
            arm_id = require_identifier(
                arm["arm_id"], f"{path}.arms[{arm_index}].arm_id"
            )
            require_identifier(
                arm["creative_id"], f"{path}.arms[{arm_index}].creative_id"
            )
            if arm["role"] not in {"treatment", "reference"}:
                raise ContractError(f"{path}.arms[{arm_index}].role is invalid")
            arm_ids.append(arm_id)
            if arm["role"] == "reference":
                reference_ids.append(arm_id)
        if (
            len(arm_ids) != len(set(arm_ids))
            or reference_ids != [estimand["reference_arm_id"]]
            or not set(treatment_ids).issubset(set(arm_ids))
        ):
            raise ContractError(f"{path}.arms do not match the estimand")
        randomization = _closed(
            cell["randomization"],
            {"mechanism", "block_ids", "batch_ids"},
            f"{path}.randomization",
        )
        if (
            randomization["mechanism"]
            != "seeded-complete-randomization-within-block"
        ):
            raise ContractError(f"{path}.randomization.mechanism is invalid")
        block_ids = _string_list(
            randomization["block_ids"],
            f"{path}.randomization.block_ids",
            nonempty=True,
        )
        batch_ids = _string_list(
            randomization["batch_ids"],
            f"{path}.randomization.batch_ids",
            nonempty=True,
        )
        if block_ids != sorted(block_ids) or batch_ids != sorted(batch_ids):
            raise ContractError(
                f"{path}.randomization identifiers must be canonically sorted"
            )
        checked_cells.append(cell)
    document["analytical_cells"] = checked_cells
    return document


def _projection_preimage(snapshot: Mapping[str, object]) -> dict[str, object]:
    result = deepcopy(dict(snapshot))
    result["library_sha256"] = None
    receipt = result.get("head_receipt")
    if isinstance(receipt, Mapping):
        receipt_copy = deepcopy(dict(receipt))
        receipt_copy["projection_sha256"] = None
        receipt_copy["receipt_sha256"] = None
        result["head_receipt"] = receipt_copy
    return result


def _linear_quantile(values: Sequence[float], probability: float) -> float:
    if not values:
        raise ContractError("quantile input must not be empty")
    ordered = sorted(values)
    position = (len(ordered) - 1) * probability
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    fraction = position - lower
    return ordered[lower] + (ordered[upper] - ordered[lower]) * fraction


def _mean(values: Sequence[float]) -> float:
    if not values:
        raise ContractError("mean input must not be empty")
    return math.fsum(values) / len(values)


def _batch_mcse(
    replicates: Sequence[float],
    *,
    batch_count: int,
    interval_level: float,
) -> dict[str, float]:
    size = len(replicates) // batch_count
    alpha = (1.0 - interval_level) / 2.0
    measures = {
        "bootstrap_mean": [],
        "interval_lower": [],
        "interval_upper": [],
    }
    for batch_index in range(batch_count):
        batch = replicates[batch_index * size : (batch_index + 1) * size]
        measures["bootstrap_mean"].append(_mean(batch))
        measures["interval_lower"].append(_linear_quantile(batch, alpha))
        measures["interval_upper"].append(
            _linear_quantile(batch, 1.0 - alpha)
        )
    result: dict[str, float] = {}
    for name, values in measures.items():
        center = _mean(values)
        variance = math.fsum((value - center) ** 2 for value in values)
        variance /= (len(values) - 1) * len(values)
        result[name] = math.sqrt(variance)
    return result


def _estimate_blocked_contrasts(
    experiment_blocks: Mapping[tuple[str, str], Sequence[float]],
    *,
    diagnosis_method: Mapping[str, object],
    monte_carlo_error_targets: Mapping[str, object],
) -> tuple[
    dict[tuple[str, str], float],
    dict[str, object],
]:
    """Apply the frozen equal-block/equal-experiment bootstrap exactly."""

    experiment_points = {
        identity: _mean(values)
        for identity, values in sorted(experiment_blocks.items())
    }
    generator = random.Random(int(diagnosis_method["bootstrap_seed"]))
    repetitions = int(diagnosis_method["bootstrap_repetitions"])
    bootstraps: dict[tuple[str, str], list[float]] = {}
    for identity in sorted(experiment_blocks):
        values = list(experiment_blocks[identity])
        if not values:
            raise ContractError("every experiment must contain complete blocks")
        bootstraps[identity] = [
            _mean(
                [
                    values[generator.randrange(len(values))]
                    for _ in range(len(values))
                ]
            )
            for _ in range(repetitions)
        ]
    combined_replicates = [
        _mean([bootstraps[identity][index] for identity in sorted(bootstraps)])
        for index in range(repetitions)
    ]
    interval_level = float(diagnosis_method["interval_level"])
    alpha = (1.0 - interval_level) / 2.0
    combined: dict[str, object] = {
        "point_estimate": _mean(list(experiment_points.values())),
        "bootstrap_mean": _mean(combined_replicates),
        "interval_lower": _linear_quantile(combined_replicates, alpha),
        "interval_upper": _linear_quantile(
            combined_replicates, 1.0 - alpha
        ),
        "interval_level": interval_level,
        "minimum_practical_effect": diagnosis_method[
            "minimum_practical_effect"
        ],
        "monte_carlo_standard_error": _batch_mcse(
            combined_replicates,
            batch_count=int(monte_carlo_error_targets["batch_count"]),
            interval_level=interval_level,
        ),
    }
    return experiment_points, combined


def _metric_value(
    entry: Mapping[str, object],
    *,
    numerator: str,
    denominator: str,
) -> float | None:
    observation = entry["observation"]
    assert isinstance(observation, Mapping)
    platform = str(entry["platform"])
    metric_id = _NUMERATOR_METRICS.get((platform, numerator))
    if metric_id is None or denominator != "impressions":
        return None
    events = [
        event
        for event in observation["outcome_events"]
        if isinstance(event, Mapping) and event.get("metric_id") == metric_id
    ]
    if len(events) != 1:
        return None
    event = events[0]
    if event["data_status"] not in {"observed", "zero"}:
        return None
    value = event["count"] if event["event_kind"] == "count" else event["value"]
    impressions = observation["delivery"]["impressions"]
    if (
        value is None
        or isinstance(value, bool)
        or not isinstance(value, (int, float))
        or isinstance(impressions, bool)
        or not isinstance(impressions, (int, float))
        or impressions <= 0
    ):
        return None
    return float(value) / float(impressions)


def _reported(value: object) -> str:
    return "not-reported" if value is None else str(value)


def _derive_informative_arm(
    *,
    design: Mapping[str, object],
    registry: Mapping[str, object],
) -> str:
    hypothesis = design["behavioral_hypothesis"]
    attribute_id = str(hypothesis["informative_attribute_id"])
    expected_value = hypothesis["informative_attribute_value"]
    creative_ids = {
        str(row["creative_id"])
        for row in registry["creative_attributes"]
        if row["attribute_id"] == attribute_id
        and row["value"] == expected_value
        and row["review_status"] == "approved"
    }
    if len(creative_ids) != 1:
        raise ContractError(
            "informative attribute must identify exactly one approved creative"
        )
    informative_creative_id = next(iter(creative_ids))
    arm_ids = {
        str(arm["arm_id"])
        for cell in design["analytical_cells"]
        for arm in cell["arms"]
        if arm["creative_id"] == informative_creative_id
        and arm["role"] == "treatment"
    }
    if len(arm_ids) != 1:
        raise ContractError(
            "experiment design must map the informative creative to one treatment arm"
        )
    return next(iter(arm_ids))


def _entry_matches_registered_cell(
    *,
    entry: Mapping[str, object],
    cell: Mapping[str, object],
    hypothesis: Mapping[str, object],
    registry: Mapping[str, object],
) -> bool:
    observation = entry["observation"]
    arm_id = str(observation["experiment_binding"]["arm_id"])
    arm_rows = [arm for arm in cell["arms"] if arm["arm_id"] == arm_id]
    if len(arm_rows) != 1:
        return False
    arm = arm_rows[0]
    creative = observation["creative_binding"]
    if creative["creative_id"] != arm["creative_id"]:
        return False
    registry_creatives = [
        row
        for row in registry["creative_bindings"]
        if row["creative_id"] == creative["creative_id"]
    ]
    if len(registry_creatives) != 1 or registry_creatives[0] != creative:
        return False
    expected_attribute = [
        row
        for row in registry["creative_attributes"]
        if row["creative_id"] == creative["creative_id"]
        and row["attribute_id"] == hypothesis["informative_attribute_id"]
    ]
    if len(expected_attribute) != 1:
        return False
    observed_attributes = [
        row
        for row in observation["creative_attribute_binding"]["attributes"]
        if row["attribute_id"] == hypothesis["informative_attribute_id"]
    ]
    expected = expected_attribute[0]
    if len(observed_attributes) != 1 or observed_attributes[0] != {
        "attribute_id": expected["attribute_id"],
        "attribute_version": expected["attribute_version"],
        "hypothesis_id": hypothesis["hypothesis_id"],
        "method_id": expected["method_id"],
        "value": expected["value"],
    }:
        return False
    measurement = observation["measurement_definition"]
    if cell["attribution"] != {
        "model": _reported(measurement["attribution_model"]),
        "click_window": _reported(measurement["click_window"]),
        "view_window": _reported(measurement["view_window"]),
        "engaged_view_window": _reported(measurement["engaged_view_window"]),
    }:
        return False
    reporting = observation["reporting_context"]
    if cell["reporting"] != {
        "report_time_basis": reporting["report_time_basis"],
        "maturity": reporting["maturity"],
        "timezone": reporting["timezone"],
        "currency": reporting["currency"],
    }:
        return False
    numerator_event = cell["estimand"]["registered_numerator_event"]
    events = [
        event
        for event in observation["outcome_events"]
        if event["metric_id"] == numerator_event["metric_id"]
    ]
    if len(events) != 1 or {
        key: events[0][key] for key in numerator_event
    } != numerator_event:
        return False
    return (
        cell["estimand"]["registered_direction"] == "higher_is_better"
        and entry["denominator_kind"]
        == cell["estimand"]["registered_denominator"]
    )


def _alternative_causes(
    value: Mapping[str, Mapping[str, object]],
) -> dict[str, dict[str, object]]:
    document = _closed(value, set(_CAUSES), "alternative_causes")
    result: dict[str, dict[str, object]] = {}
    for key in _CAUSES:
        cause = _closed(
            document[key],
            {"status", "evidence_sha256", "rationale"},
            f"alternative_causes.{key}",
        )
        if cause["status"] not in {"cleared", "not_cleared", "unknown"}:
            raise ContractError(f"alternative_causes.{key}.status is invalid")
        digest = cause["evidence_sha256"]
        if (
            not isinstance(digest, str)
            or not digest.startswith("sha256:")
            or len(digest) != 71
        ):
            raise ContractError(
                f"alternative_causes.{key}.evidence_sha256 is invalid"
            )
        _string(cause["rationale"], f"alternative_causes.{key}.rationale")
        result[key] = cause
    return result


def _analysis_status(
    *,
    experiment_points: Mapping[tuple[str, str], float],
    combined: Mapping[str, object],
    minimum_practical_effect: float,
) -> str:
    """Separate material directional signal from combined eligibility."""

    material_signal = any(
        point > minimum_practical_effect
        for point in experiment_points.values()
    )
    if not material_signal:
        return "no_miss"
    if any(point < 0.0 for point in experiment_points.values()):
        return "contradictory"
    if (
        float(combined["point_estimate"]) > minimum_practical_effect
        and float(combined["interval_lower"]) > 0.0
    ):
        return "eligible"
    return "no_miss"


def _reconcile_hypothesis_strata(
    evaluated: Sequence[tuple[
        str,
        dict[str, object],
        dict[str, object],
        dict[str, object],
    ]],
    *,
    manifest: Mapping[str, object],
) -> list[tuple[
    str,
    dict[str, object],
    dict[str, object],
    dict[str, object],
]]:
    by_hypothesis: dict[str, list[tuple[
        str,
        dict[str, object],
        dict[str, object],
        dict[str, object],
    ]]] = defaultdict(list)
    for item in evaluated:
        by_hypothesis[str(item[1]["hypothesis_id"])].append(item)
    reconciled = []
    for hypothesis_id in sorted(by_hypothesis):
        items = by_hypothesis[hypothesis_id]
        experiment_blocks: dict[tuple[str, str], Sequence[float]] = {}
        evidence_rows: list[dict[str, object]] = []
        strata: list[dict[str, object]] = []
        for status, hypothesis, _, detail in items:
            for identity, values in detail["experiment_blocks"].items():
                if identity in experiment_blocks:
                    raise ContractError(
                        "same-hypothesis strata must bind disjoint experiments"
                    )
                experiment_blocks[identity] = values
            evidence_rows.extend(detail["evidence_rows"])
            strata.append(
                {
                    "compatibility_key_sha256": hypothesis[
                        "compatibility_key_sha256"
                    ],
                    "status": status,
                    "experiment_bindings": [
                        {
                            "experiment_id": identity[0],
                            "campaign_id": identity[1],
                        }
                        for identity in sorted(detail["experiment_blocks"])
                    ],
                    "evidence_entry_ids": sorted(
                        {
                            str(row["entry_id"])
                            for row in detail["evidence_rows"]
                        }
                    ),
                    "evidence_sha256": sorted(
                        {
                            str(row["entry_sha256"])
                            for row in detail["evidence_rows"]
                        }
                    ),
                }
            )
        strata.sort(key=lambda row: str(row["compatibility_key_sha256"]))
        combined = None
        experiment_points = {
            identity: _mean(values)
            for identity, values in experiment_blocks.items()
            if values
        }
        if (
            experiment_blocks
            and len(experiment_points) == len(experiment_blocks)
        ):
            experiment_points, combined = _estimate_blocked_contrasts(
                experiment_blocks,
                diagnosis_method=manifest["diagnosis_method"],
                monte_carlo_error_targets=manifest[
                    "monte_carlo_error_targets"
                ],
            )
        if any(item[0] == "insufficient" for item in items):
            reconciled_status = "insufficient"
        elif combined is None:
            reconciled_status = "insufficient"
        else:
            reconciled_status = _analysis_status(
                experiment_points=experiment_points,
                combined=combined,
                minimum_practical_effect=float(
                    manifest["diagnosis_method"][
                        "minimum_practical_effect"
                    ]
                ),
            )
        hypothesis = deepcopy(items[0][1])
        hypothesis["compatibility_key_sha256"] = sha256_json(
            sorted(
                str(item[1]["compatibility_key_sha256"])
                for item in items
            )
        )
        reconciled.append(
            (
                reconciled_status,
                hypothesis,
                items[0][2],
                {
                    "experiment_blocks": experiment_blocks,
                    "experiment_points": experiment_points,
                    "evidence_rows": evidence_rows,
                    "combined": combined,
                    "strata": strata,
                },
            )
        )
    return reconciled


def _bindings(
    *,
    manifest: Mapping[str, object],
    designs: Sequence[Mapping[str, object]],
    registry: Mapping[str, object],
    snapshot: Mapping[str, object],
    receipt: Mapping[str, object],
    estimand: Mapping[str, object] | None,
) -> tuple[dict[str, object], dict[str, object], dict[str, object]]:
    projection = {
        "library_id": snapshot["library_id"],
        "as_of": snapshot["as_of"],
        "library_sha256": snapshot["library_sha256"],
        "projection_sha256": receipt["projection_sha256"],
    }
    receipt_binding = {
        "receipt_id": receipt["receipt_id"],
        "receipt_sha256": receipt["receipt_sha256"],
        "event_count": receipt["event_count"],
        "event_sha256": receipt["event_sha256"],
        "projection_sha256": receipt["projection_sha256"],
    }
    frozen = {
        "diagnosis_method_version": DIAGNOSIS_METHOD_VERSION,
        "diagnosis_method_sha256": sha256_json(manifest["diagnosis_method"]),
        "monte_carlo_error_method_version": manifest[
            "monte_carlo_error_targets"
        ]["method_version"],
        "monte_carlo_error_method_sha256": sha256_json(
            manifest["monte_carlo_error_targets"]
        ),
        "estimand_sha256": sha256_json(estimand or {"status": "not_selected"}),
        "stopping_rule_sha256": sha256_json(manifest["stopping_rule"]),
        "experiment_design_sha256": sorted(
            str(design["design_sha256"]) for design in designs
        ),
        "creative_attribute_registry_sha256": registry["registry_sha256"],
    }
    return projection, receipt_binding, frozen


def _diagnosis_document(
    *,
    diagnosis_id: str,
    diagnosed_at: str,
    decision: str,
    base_panel_binding: Mapping[str, object],
    manifest: Mapping[str, object],
    designs: Sequence[Mapping[str, object]],
    registry: Mapping[str, object],
    snapshot: Mapping[str, object],
    receipt: Mapping[str, object],
    causes: Mapping[str, Mapping[str, object]],
    estimand: Mapping[str, object] | None,
    target_persona_id: str | None,
    eligible_hypothesis_ids: list[str],
    selected_hypothesis: dict[str, object] | None,
    analysis: dict[str, object] | None,
    limitations: list[str],
) -> dict[str, object]:
    projection, receipt_binding, frozen = _bindings(
        manifest=manifest,
        designs=designs,
        registry=registry,
        snapshot=snapshot,
        receipt=receipt,
        estimand=estimand,
    )
    document: dict[str, object] = {
        "schema_version": DIAGNOSIS_VERSION,
        "diagnosis_id": diagnosis_id,
        "diagnosed_at": diagnosed_at,
        "decision": decision,
        "base_panel_binding": deepcopy(dict(base_panel_binding)),
        "base_panel_authority_status": "unverified_proposal_context",
        "synthetic_study_binding": {
            "study_id": manifest["study_id"],
            "study_manifest_sha256": manifest["manifest_sha256"],
        },
        "evidence_library_projection_binding": projection,
        "evidence_head_receipt_binding": receipt_binding,
        "frozen_analysis_bindings": frozen,
        "target_persona_id": target_persona_id,
        "eligible_hypothesis_ids": sorted(eligible_hypothesis_ids),
        "selected_hypothesis": selected_hypothesis,
        "analysis": analysis,
        "alternative_causes": deepcopy(dict(causes)),
        "limitations": limitations,
        "diagnosis_sha256": None,
    }
    document["diagnosis_sha256"] = sha256_json(document)
    return validate_diagnosis(document)


def diagnose_persona_behavior(
    *,
    base_panel_binding: Mapping[str, object],
    study_manifest: dict[str, object],
    scenario_manifests: Sequence[dict[str, object]],
    experiment_designs: Sequence[dict[str, object]],
    evidence_library_snapshot: dict[str, object],
    evidence_head_receipt: dict[str, object],
    attribute_registry: dict[str, object],
    alternative_causes: Mapping[str, Mapping[str, object]],
    diagnosis_id: str,
    diagnosed_at: str,
) -> dict[str, object]:
    """Return one frozen synthetic diagnosis without mutating caller inputs."""

    diagnosis_identifier = require_identifier(diagnosis_id, "diagnosis_id")
    diagnosis_timestamp = (
        require_timestamp(diagnosed_at, "diagnosed_at")
        .isoformat()
        .replace("+00:00", "Z")
    )
    panel = _closed(
        base_panel_binding,
        {
            "panel_id",
            "panel_version",
            "panel_sha256",
            "persona_id",
            "persona_snapshot_sha256",
        },
        "base_panel_binding",
    )
    for field in ("panel_id", "persona_id"):
        require_identifier(panel[field], f"base_panel_binding.{field}")
    manifest = validate_study_manifest(study_manifest)
    causes = _alternative_causes(alternative_causes)

    # Evidence-facing failures produce an auditable invalid diagnosis instead
    # of admitting caller-selected rows.  Frozen-method contract failures above
    # remain ordinary contract errors.
    try:
        registry = validate_creative_attribute_registry(attribute_registry)
        snapshot = validate_evidence_library(evidence_library_snapshot)
        receipt = validate_evidence_receipt(evidence_head_receipt)
        checked_scenario_manifests = [
            _validate_scenario_manifest(value, manifest=manifest)
            for value in scenario_manifests
        ]
        if not checked_scenario_manifests:
            raise ContractError("scenario_manifests must be non-empty")
        scenario_manifest_by_id = {
            str(value["scenario_binding"]["scenario_id"]): value
            for value in checked_scenario_manifests
        }
        if len(scenario_manifest_by_id) != len(checked_scenario_manifests):
            raise ContractError("scenario_manifests must bind unique scenarios")
        designs = []
        for raw_design in experiment_designs:
            if not isinstance(raw_design, Mapping) or not isinstance(
                raw_design.get("scenario_binding"), Mapping
            ):
                raise ContractError(
                    "experiment design must contain a scenario binding"
                )
            scenario_id = str(
                raw_design["scenario_binding"].get("scenario_id", "")
            )
            scenario_manifest = scenario_manifest_by_id.get(scenario_id)
            if scenario_manifest is None:
                raise ContractError(
                    "experiment design has no authenticated scenario manifest"
                )
            designs.append(
                _validate_design(
                    raw_design,
                    manifest=manifest,
                    registry=registry,
                    scenario_manifest=scenario_manifest,
                )
            )
        if not designs:
            raise ContractError("experiment_designs must be non-empty")
        if {
            str(design["scenario_binding"]["scenario_id"]) for design in designs
        } != set(scenario_manifest_by_id):
            raise ContractError(
                "scenario_manifests and experiment_designs must bind the same scenarios"
            )
        designs.sort(key=lambda design: str(design["design_id"]))
    except ContractError:
        # Self-hashed fixture inputs still provide enough binding material for
        # the closed invalid result.  If they do not, the input is malformed
        # rather than merely invalid evidence and must fail as a contract.
        registry = deepcopy(attribute_registry)
        snapshot = deepcopy(evidence_library_snapshot)
        receipt = deepcopy(evidence_head_receipt)
        designs = [deepcopy(design) for design in experiment_designs]
        required = (
            isinstance(registry.get("registry_sha256"), str)
            and isinstance(snapshot.get("library_sha256"), str)
            and isinstance(snapshot.get("library_id"), str)
            and isinstance(snapshot.get("as_of"), str)
            and isinstance(receipt.get("receipt_sha256"), str)
            and isinstance(receipt.get("projection_sha256"), str)
            and bool(designs)
            and all(
                isinstance(design, Mapping)
                and isinstance(design.get("design_sha256"), str)
                for design in designs
            )
        )
        if not required:
            raise
        return _diagnosis_document(
            diagnosis_id=diagnosis_identifier,
            diagnosed_at=diagnosis_timestamp,
            decision="invalid_evidence",
            base_panel_binding=panel,
            manifest=manifest,
            designs=designs,
            registry=registry,
            snapshot=snapshot,
            receipt=receipt,
            causes=causes,
            estimand=None,
            target_persona_id=None,
            eligible_hypothesis_ids=[],
            selected_hypothesis=None,
            analysis=None,
            limitations=["Input evidence or its preregistration is invalid."],
        )

    authenticated = (
        snapshot["head_receipt"] == receipt
        and receipt["library_id"] == snapshot["library_id"]
        and receipt["event_count"] == snapshot["event_count"]
        and receipt["projection_sha256"]
        == sha256_json(_projection_preimage(snapshot))
    )
    if not authenticated:
        return _diagnosis_document(
            diagnosis_id=diagnosis_identifier,
            diagnosed_at=diagnosis_timestamp,
            decision="invalid_evidence",
            base_panel_binding=panel,
            manifest=manifest,
            designs=designs,
            registry=registry,
            snapshot=snapshot,
            receipt=receipt,
            causes=causes,
            estimand=None,
            target_persona_id=None,
            eligible_hypothesis_ids=[],
            selected_hypothesis=None,
            analysis=None,
            limitations=[
                "The Outcome Evidence Library projection does not authenticate "
                "to the supplied historical receipt."
            ],
        )
    entries = list(snapshot["entries"])
    dependencies = [entry["dependency_identity_sha256"] for entry in entries]
    if len(dependencies) != len(set(dependencies)):
        return _diagnosis_document(
            diagnosis_id=diagnosis_identifier,
            diagnosed_at=diagnosis_timestamp,
            decision="invalid_evidence",
            base_panel_binding=panel,
            manifest=manifest,
            designs=designs,
            registry=registry,
            snapshot=snapshot,
            receipt=receipt,
            causes=causes,
            estimand=None,
            target_persona_id=None,
            eligible_hypothesis_ids=[],
            selected_hypothesis=None,
            analysis=None,
            limitations=["Active evidence contains a dependent duplicate."],
        )
    admitted_hashes = {
        str(entry["entry_sha256"]) for entry in entries
    } | {str(design["design_sha256"]) for design in designs}
    if any(
        cause["status"] == "cleared"
        and cause["evidence_sha256"] not in admitted_hashes
        for cause in causes.values()
    ):
        return _diagnosis_document(
            diagnosis_id=diagnosis_identifier,
            diagnosed_at=diagnosis_timestamp,
            decision="invalid_evidence",
            base_panel_binding=panel,
            manifest=manifest,
            designs=designs,
            registry=registry,
            snapshot=snapshot,
            receipt=receipt,
            causes=causes,
            estimand=None,
            target_persona_id=None,
            eligible_hypothesis_ids=[],
            selected_hypothesis=None,
            analysis=None,
            limitations=["A cleared alternative cause cites unauthenticated evidence."],
        )
    if any(cause["status"] != "cleared" for cause in causes.values()):
        return _diagnosis_document(
            diagnosis_id=diagnosis_identifier,
            diagnosed_at=diagnosis_timestamp,
            decision="alternative_cause_not_cleared",
            base_panel_binding=panel,
            manifest=manifest,
            designs=designs,
            registry=registry,
            snapshot=snapshot,
            receipt=receipt,
            causes=causes,
            estimand=None,
            target_persona_id=None,
            eligible_hypothesis_ids=[],
            selected_hypothesis=None,
            analysis=None,
            limitations=["At least one alternative cause is not cleared."],
        )
    if any(
        entry["study_manifest_sha256"] != manifest["manifest_sha256"]
        or entry["creative_attribute_registry_sha256"]
        != registry["registry_sha256"]
        for entry in entries
    ):
        return _diagnosis_document(
            diagnosis_id=diagnosis_identifier,
            diagnosed_at=diagnosis_timestamp,
            decision="invalid_evidence",
            base_panel_binding=panel,
            manifest=manifest,
            designs=designs,
            registry=registry,
            snapshot=snapshot,
            receipt=receipt,
            causes=causes,
            estimand=None,
            target_persona_id=None,
            eligible_hypothesis_ids=[],
            selected_hypothesis=None,
            analysis=None,
            limitations=["Evidence study or registry bindings are inconsistent."],
        )

    registered_hypotheses: dict[str, tuple[str, dict[str, object]]] = {}
    for definition in registry["attribute_definitions"]:
        hypothesis = definition["behavioral_hypothesis"]
        if hypothesis is not None:
            registered_hypotheses[str(hypothesis["hypothesis_id"])] = (
                str(definition["attribute_id"]),
                hypothesis,
            )
    candidates: list[
        tuple[
            dict[str, object],
            dict[str, object],
            dict[str, object],
            list[dict[str, object]],
        ]
    ] = []
    for design in designs:
        design_hypothesis = design["behavioral_hypothesis"]
        registered = registered_hypotheses.get(
            str(design_hypothesis["hypothesis_id"])
        )
        if registered is None:
            return _diagnosis_document(
                diagnosis_id=diagnosis_identifier,
                diagnosed_at=diagnosis_timestamp,
                decision="invalid_evidence",
                base_panel_binding=panel,
                manifest=manifest,
                designs=designs,
                registry=registry,
                snapshot=snapshot,
                receipt=receipt,
                causes=causes,
                estimand=None,
                target_persona_id=None,
                eligible_hypothesis_ids=[],
                selected_hypothesis=None,
                analysis=None,
                limitations=["A design hypothesis is not preregistered."],
            )
        attribute_id, registry_hypothesis = registered
        if (
            design_hypothesis["informative_attribute_id"] != attribute_id
            or
            design_hypothesis["target_persona_id"]
            != registry_hypothesis["target_persona_id"]
            or design_hypothesis["target_field"]
            != registry_hypothesis["target_persona_field"]
        ):
            return _diagnosis_document(
                diagnosis_id=diagnosis_identifier,
                diagnosed_at=diagnosis_timestamp,
                decision="invalid_evidence",
                base_panel_binding=panel,
                manifest=manifest,
                designs=designs,
                registry=registry,
                snapshot=snapshot,
                receipt=receipt,
                causes=causes,
                estimand=None,
                target_persona_id=None,
                eligible_hypothesis_ids=[],
                selected_hypothesis=None,
                analysis=None,
                limitations=["Design and registry hypothesis targets conflict."],
            )
        try:
            informative_arm_id = _derive_informative_arm(
                design=design,
                registry=registry,
            )
        except ContractError:
            return _diagnosis_document(
                diagnosis_id=diagnosis_identifier,
                diagnosed_at=diagnosis_timestamp,
                decision="invalid_evidence",
                base_panel_binding=panel,
                manifest=manifest,
                designs=designs,
                registry=registry,
                snapshot=snapshot,
                receipt=receipt,
                causes=causes,
                estimand=None,
                target_persona_id=None,
                eligible_hypothesis_ids=[],
                selected_hypothesis=None,
                analysis=None,
                limitations=[
                    "The preregistered informative attribute does not identify "
                    "one design treatment arm."
                ],
            )
        grouped: dict[str, list[dict[str, object]]] = defaultdict(list)
        for cell in design["analytical_cells"]:
            compatibility = {
                "target_persona_id": design_hypothesis["target_persona_id"],
                "segment_id": cell["segment_id"],
                "platform": cell["platform"],
                "objective": cell["objective"],
                "placement": cell["placement"],
                "estimand": cell["estimand"],
                "attribution": cell["attribution"],
                "reporting": cell["reporting"],
                "creative_attribute_registry_sha256": registry["registry_sha256"],
            }
            grouped[sha256_json(compatibility)].append(cell)
        for compatibility_hash, cells in grouped.items():
            cell_experiments = {
                (cell["experiment_id"], cell["campaign_id"]) for cell in cells
            }
            if len(cell_experiments) < int(
                manifest["diagnosis_method"]["minimum_independent_experiments"]
            ):
                continue
            matching = [
                entry
                for entry in entries
                if design_hypothesis["target_persona_id"] in entry["persona_ids"]
                and any(
                    entry["experiment_id"] == cell["experiment_id"]
                    and entry["campaign_id"] == cell["campaign_id"]
                    and entry["platform"] == cell["platform"]
                    and entry["segment_id"] == cell["segment_id"]
                    and entry["objective"] == cell["objective"]
                    and entry["placement"] == cell["placement"]
                    and entry["block_id"]
                    in cell["randomization"]["block_ids"]
                    and entry["batch_id"]
                    in cell["randomization"]["batch_ids"]
                    and entry["denominator_kind"]
                    == cell["estimand"]["registered_denominator"]
                    and entry["evidence_maturity"]
                    == cell["reporting"]["maturity"]
                    and entry["design"] == "randomized"
                    and _entry_matches_registered_cell(
                        entry=entry,
                        cell=cell,
                        hypothesis=design_hypothesis,
                        registry=registry,
                    )
                    for cell in cells
                )
            ]
            if matching:
                registered_numerator = str(
                    cells[0]["estimand"]["registered_numerator"]
                )
                numerator_metric = cells[0]["estimand"][
                    "registered_numerator_event"
                ]["metric_id"]
                semantic_keys = {
                    sha256_json(
                        {
                            "registered_numerator": registered_numerator,
                            "numerator_event": next(
                                (
                                    {
                                        "metric_id": event["metric_id"],
                                        "event_kind": event["event_kind"],
                                        "attribution_kind": event[
                                            "attribution_kind"
                                        ],
                                        "report_time_basis": event[
                                            "report_time_basis"
                                        ],
                                    }
                                    for event in entry["observation"][
                                        "outcome_events"
                                    ]
                                    if event["metric_id"] == numerator_metric
                                ),
                                None,
                            ),
                            "denominator_kind": entry["denominator_kind"],
                            "evidence_maturity": entry["evidence_maturity"],
                            "currency": entry["metric_identity"]["currency"],
                            "timezone": entry["metric_identity"]["timezone"],
                        }
                    )
                    for entry in matching
                }
                if len(semantic_keys) != 1:
                    continue
                candidates.append(
                    (
                        design,
                        {
                            **design_hypothesis,
                            "attribute_id": attribute_id,
                            "informative_arm_id": informative_arm_id,
                            "registry_hypothesis": registry_hypothesis,
                            "compatibility_key_sha256": sha256_json(
                                {
                                    "design_compatibility_sha256": compatibility_hash,
                                    "platform_semantics_sha256": next(
                                        iter(semantic_keys)
                                    ),
                                }
                            ),
                        },
                        cells[0]["estimand"],
                        matching,
                    )
                )

    if not candidates:
        return _diagnosis_document(
            diagnosis_id=diagnosis_identifier,
            diagnosed_at=diagnosis_timestamp,
            decision="insufficient_evidence",
            base_panel_binding=panel,
            manifest=manifest,
            designs=designs,
            registry=registry,
            snapshot=snapshot,
            receipt=receipt,
            causes=causes,
            estimand=None,
            target_persona_id=None,
            eligible_hypothesis_ids=[],
            selected_hypothesis=None,
            analysis=None,
            limitations=["No compatible two-experiment evidence stratum exists."],
        )

    evaluated: list[
        tuple[
            str,
            dict[str, object],
            dict[str, object],
            dict[str, object],
        ]
    ] = []
    for design, hypothesis, estimand, matching in candidates:
        cells_by_experiment = {
            (str(cell["experiment_id"]), str(cell["campaign_id"])): cell
            for cell in design["analytical_cells"]
            if any(
                cell["experiment_id"] == entry["experiment_id"]
                and cell["campaign_id"] == entry["campaign_id"]
                and cell["platform"] == entry["platform"]
                and cell["segment_id"] == entry["segment_id"]
                for entry in matching
            )
        }
        selected_block_sets = [
            set(cell["randomization"]["block_ids"])
            for cell in cells_by_experiment.values()
        ]
        if any(
            left & right
            for index, left in enumerate(selected_block_sets)
            for right in selected_block_sets[index + 1 :]
        ):
            evaluated.append(
                (
                    "insufficient",
                    hypothesis,
                    estimand,
                    {"experiment_blocks": {}, "evidence_rows": []},
                )
            )
            continue
        experiment_blocks: dict[tuple[str, str], list[float]] = {}
        evidence_rows: list[dict[str, object]] = []
        incomplete = False
        for experiment_identity in sorted(cells_by_experiment):
            cell = cells_by_experiment[experiment_identity]
            by_block: dict[str, dict[str, dict[str, object]]] = defaultdict(dict)
            for entry in matching:
                if (
                    entry["experiment_id"],
                    entry["campaign_id"],
                ) != experiment_identity:
                    continue
                arm_id = str(entry["observation"]["experiment_binding"]["arm_id"])
                if arm_id in by_block[str(entry["block_id"])]:
                    incomplete = True
                    break
                by_block[str(entry["block_id"])][arm_id] = entry
            values: list[float] = []
            informative_arm = str(hypothesis["informative_arm_id"])
            reference_arm = str(cell["estimand"]["reference_arm_id"])
            expected_arm_ids = {
                str(arm["arm_id"]) for arm in cell["arms"]
            }
            for block_id in cell["randomization"]["block_ids"]:
                arms = by_block.get(str(block_id), {})
                if set(arms) != expected_arm_ids:
                    incomplete = True
                    continue
                treated = _metric_value(
                    arms[informative_arm],
                    numerator=str(cell["estimand"]["registered_numerator"]),
                    denominator=str(cell["estimand"]["registered_denominator"]),
                )
                reference = _metric_value(
                    arms[reference_arm],
                    numerator=str(cell["estimand"]["registered_numerator"]),
                    denominator=str(cell["estimand"]["registered_denominator"]),
                )
                if treated is None or reference is None:
                    incomplete = True
                    continue
                values.append(treated - reference)
                evidence_rows.extend((arms[informative_arm], arms[reference_arm]))
            if len(values) < int(
                manifest["diagnosis_method"][
                    "minimum_complete_blocks_per_experiment"
                ]
            ):
                incomplete = True
            experiment_blocks[experiment_identity] = values
        if incomplete or len(experiment_blocks) < int(
            manifest["diagnosis_method"]["minimum_independent_experiments"]
        ):
            evaluated.append(
                (
                    "insufficient",
                    hypothesis,
                    estimand,
                    {
                        "experiment_blocks": experiment_blocks,
                        "evidence_rows": evidence_rows,
                    },
                )
            )
            continue
        experiment_points = {
            identity: _mean(values)
            for identity, values in experiment_blocks.items()
        }
        practical_threshold = float(
            manifest["diagnosis_method"]["minimum_practical_effect"]
        )
        experiment_points, combined = _estimate_blocked_contrasts(
            experiment_blocks,
            diagnosis_method=manifest["diagnosis_method"],
            monte_carlo_error_targets=manifest["monte_carlo_error_targets"],
        )
        analysis_status = _analysis_status(
            experiment_points=experiment_points,
            combined=combined,
            minimum_practical_effect=practical_threshold,
        )
        if analysis_status == "contradictory":
            evaluated.append(
                (
                    "contradictory",
                    hypothesis,
                    estimand,
                    {
                        "experiment_blocks": experiment_blocks,
                        "experiment_points": experiment_points,
                        "evidence_rows": evidence_rows,
                        "combined": combined,
                    },
                )
            )
            continue
        evaluated.append(
            (
                analysis_status,
                hypothesis,
                estimand,
                {
                    "experiment_blocks": experiment_blocks,
                    "experiment_points": experiment_points,
                    "evidence_rows": evidence_rows,
                    "combined": combined,
                },
            )
        )

    evaluated = _reconcile_hypothesis_strata(
        evaluated,
        manifest=manifest,
    )
    eligible = [item for item in evaluated if item[0] == "eligible"]
    contradictory = any(
        item[0] in {"contradictory", "ambiguous"} for item in evaluated
    )
    if len(eligible) > 1 or contradictory:
        status = "non_identifiable"
        chosen = eligible[0] if eligible else next(
            item
            for item in evaluated
            if item[0] in {"contradictory", "ambiguous"}
        )
        selected = None
        eligible_ids: list[str] = []
        target = None
    elif len(eligible) == 1:
        chosen = eligible[0]
        hypothesis = chosen[1]
        if hypothesis["target_persona_id"] != panel["persona_id"]:
            status = "invalid_evidence"
            selected = None
            eligible_ids = []
            target = None
        else:
            status = "repeatable_behavioral_miss"
            evidence_rows = chosen[3]["evidence_rows"]
            selected = {
                "hypothesis_id": hypothesis["hypothesis_id"],
                "attribute_id": hypothesis["attribute_id"],
                "target_persona_id": hypothesis["target_persona_id"],
                "target_persona_field": hypothesis["target_field"],
                "proposed_value": deepcopy(
                    hypothesis["registry_hypothesis"]["proposed_value"]
                ),
                "rationale_template": hypothesis["registry_hypothesis"][
                    "rationale_template"
                ],
                "evidence_entry_ids": sorted(
                    {str(row["entry_id"]) for row in evidence_rows}
                ),
                "evidence_sha256": sorted(
                    {str(row["entry_sha256"]) for row in evidence_rows}
                ),
            }
            eligible_ids = [str(hypothesis["hypothesis_id"])]
            target = str(hypothesis["target_persona_id"])
    elif any(item[0] == "no_miss" for item in evaluated):
        status = "no_repeatable_miss"
        chosen = next(item for item in evaluated if item[0] == "no_miss")
        selected = None
        eligible_ids = []
        target = None
    else:
        status = "insufficient_evidence"
        chosen = evaluated[0]
        selected = None
        eligible_ids = []
        target = None

    detail = chosen[3]
    analysis = None
    if detail.get("experiment_points") and detail.get("combined"):
        experiment_points = detail["experiment_points"]
        experiment_blocks = detail["experiment_blocks"]
        analysis = {
            "compatibility_key_sha256": chosen[1][
                "compatibility_key_sha256"
            ],
            "independent_experiment_count": len(experiment_points),
            "complete_blocks_per_experiment": [
                {
                    "experiment_id": identity[0],
                    "campaign_id": identity[1],
                    "complete_block_count": len(experiment_blocks[identity]),
                }
                for identity in sorted(experiment_blocks)
            ],
            "experiments": [
                {
                    "experiment_id": identity[0],
                    "campaign_id": identity[1],
                    "point_estimate": experiment_points[identity],
                }
                for identity in sorted(experiment_points)
            ],
            "combined": detail["combined"],
            "strata": deepcopy(detail.get("strata", [])),
            "association_claim": (
                "synthetic_creative_feature_associated_with_registered_outcome"
            ),
        }
    return _diagnosis_document(
        diagnosis_id=diagnosis_identifier,
        diagnosed_at=diagnosis_timestamp,
        decision=status,
        base_panel_binding=panel,
        manifest=manifest,
        designs=designs,
        registry=registry,
        snapshot=snapshot,
        receipt=receipt,
        causes=causes,
        estimand=chosen[2],
        target_persona_id=target,
        eligible_hypothesis_ids=eligible_ids,
        selected_hypothesis=selected,
        analysis=analysis,
        limitations=[
            "Fictional synthetic fixtures only; this is not real-world "
            "validation, calibration, improvement, causation, or preference."
        ],
    )
