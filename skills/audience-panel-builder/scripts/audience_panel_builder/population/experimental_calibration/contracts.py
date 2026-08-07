"""Closed, canonical documents for synthetic-only behavior experiments.

This module deliberately contains no production package or registration
imports.  Its documents are an isolated experimental boundary.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Mapping
from copy import deepcopy
import hashlib
import math
import re
import sys

from ...common import (
    canonical_json_bytes,
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


OUTCOME_OBSERVATION_VERSION = "persona-behavior-outcome-observation-v1"
STUDY_MANIFEST_VERSION = "synthetic-persona-behavior-study-manifest-v1"
ATTRIBUTE_REGISTRY_VERSION = "creative-attribute-registry-v1"
EVIDENCE_LIBRARY_VERSION = "persona-behavior-evidence-library-v1"
EVIDENCE_LIBRARY_INDEX_VERSION = "persona-behavior-evidence-library-index-v1"
EVIDENCE_ENTRY_VERSION = "persona-behavior-evidence-entry-v1"
EVIDENCE_EVENT_VERSION = "persona-behavior-evidence-event-v1"
EVIDENCE_RECEIPT_VERSION = "persona-behavior-evidence-receipt-v1"
DIAGNOSIS_VERSION = "experimental-persona-behavior-diagnosis-v1"
DIAGNOSIS_METHOD_VERSION = "blocked-contrast-bootstrap-v1"
PROPOSAL_VERSION = "experimental-persona-behavior-proposal-v1"
CANDIDATE_VERSION = "experimental-persona-panel-candidate-v1"
AUTHORING_PROJECTION_VERSION = "experimental-persona-authoring-projection-v1"
EXERCISE_VERSION = "synthetic-persona-behavior-exercise-v1"
SYNTHETIC_STUDY_GENERATOR_VERSION = "1.0.0"
_ADVERSARIAL_SCENARIO_SPECS = {
    "attribution-mismatch": ("attribution-mismatch", "open", 4003),
    "block-reversal": ("block-reversal", "open", 4008),
    "breakdown-double-count": ("breakdown-double-count", "open", 4007),
    "contradictory-history": ("contradictory-history", "open", 4017),
    "creative-attribute-ambiguity": (
        "creative-attribute-ambiguity",
        "open",
        4016,
    ),
    "currency-timezone-mismatch": (
        "currency-timezone-mismatch",
        "open",
        4020,
    ),
    "delayed-censored-outcomes": (
        "delayed-censored-outcomes",
        "open",
        4013,
    ),
    "denominator-trap": ("denominator-trap", "open", 4002),
    "duplicate-evidence": ("duplicate-evidence", "open", 4018),
    "heavy-tailed-revenue": ("heavy-tailed-revenue", "open", 4010),
    "late-maturation": ("late-maturation", "open", 4004),
    "missing-persona": ("missing-persona", "open", 4021),
    "modeled-fractional": ("modeled-fractional", "open", 4005),
    "multiple-hypotheses": ("multiple-hypotheses", "open", 4023),
    "nonlinear-saturation": ("nonlinear-saturation", "open", 4012),
    "observational-confounding": (
        "observational-confounding",
        "open",
        4015,
    ),
    "platform-interaction": ("platform-interaction", "open", 4001),
    "sealed-holdout-reuse": ("sealed-holdout-reuse", "sealed", 4024),
    "small-sample": ("small-sample", "open", 4009),
    "structural-change-request": (
        "structural-change-request",
        "open",
        4022,
    ),
    "suppressed-missing": ("suppressed-missing", "open", 4006),
    "tampered-hash": ("tampered-hash", "open", 4019),
    "temporal-drift": ("temporal-drift", "open", 4014),
    "zero-inflated-value": ("zero-inflated-value", "open", 4011),
}
SYNTHETIC_SCENARIO_REGISTRY = {
    "known-proof-need-miss": {
        "dgp_id": "heterogeneous-cfo-proof-need",
        "dgp_version": "1.0.0",
        "partition": "open",
    },
    "non-identifiable-twin-a": {
        "dgp_id": "non-identifiable-twin-a",
        "dgp_version": "1.0.0",
        "partition": "sealed",
    },
    "non-identifiable-twin-b": {
        "dgp_id": "non-identifiable-twin-b",
        "dgp_version": "1.0.0",
        "partition": "sealed",
    },
    "null-effect": {
        "dgp_id": "randomized-block-null",
        "dgp_version": "1.0.0",
        "partition": "open",
    },
    **{
        scenario_id: {
            "dgp_id": dgp_id,
            "dgp_version": "1.0.0",
            "partition": partition,
        }
        for scenario_id, (
            dgp_id,
            partition,
            _seed,
        ) in _ADVERSARIAL_SCENARIO_SPECS.items()
    },
}
SYNTHETIC_SCENARIO_MANIFEST_SHA256 = {
    "attribution-mismatch": "sha256:ad26a060eae4a541d7b6367d8e5f0336dd6071d764fc9328e99ecc3b6323e6b6",
    "block-reversal": "sha256:42092d0b970946271d63982de6db9dda1cd35007450bdf76282f6a5a57edf99d",
    "breakdown-double-count": "sha256:54891db6239fc3b9942fb31ae5265c3d34cd6e5d2ed47c8071d1a8e0d5149cba",
    "contradictory-history": "sha256:22dd036cb3d2e005efc245be72e54c825397c2c34d44048e1d0806c1c488bc25",
    "creative-attribute-ambiguity": "sha256:7137d29cd48b7c3554731c2fbfae5ed6579f3c88ec831334dd297d3978834d52",
    "currency-timezone-mismatch": "sha256:4e57cda98884bc93280eb303d1920fca647776cc26b2ce7332b80dcde94c3b5a",
    "delayed-censored-outcomes": "sha256:1b68a192f51a878a123f621469e38f2e003178695b0662a974d6619bea76b94e",
    "denominator-trap": "sha256:90b0ad91cfe2d4fcfaf7cd9b6f70c51663f92833960e26a8598f93b2d92bc36a",
    "duplicate-evidence": "sha256:f5c3c725dc68e4857b2dcaaa0fca6ec553add577e136b37f0cfe57ef1da27c4f",
    "heavy-tailed-revenue": "sha256:99c8232f9373b1cbb8081f7e4787af24ac125bdfcf0ff9c64d1f188fd13eda7f",
    "known-proof-need-miss": "sha256:4dc8573a696d43d7410a7091ffe43cae49162b6fbd25afb9ae3173b712f250f6",
    "late-maturation": "sha256:ae0592d595317eea48c5219fd9a6ef1051142d6321c9732204e7552149f2e92c",
    "missing-persona": "sha256:662deeae78ee35615dd42ac0e79949ad5966e9c24e707d697ac8e5bb9d4982fa",
    "modeled-fractional": "sha256:901b1b28ca74b0368da3a7148d804f858d2a22e8ccba0c5cf81d80914b21a97c",
    "multiple-hypotheses": "sha256:ba8c190a70a487feb3e6ce003abcf0112ee17c304b84218f982b90bc0e7722ee",
    "non-identifiable-twin-a": "sha256:0f22d030d3cddbc141aa70ad52b3e0dd419c9039bd3d732896c401d4c7da1fd6",
    "non-identifiable-twin-b": "sha256:1d0e002c5d664660c762e2af7f442b10421347ed1c36922ebdbe06a4a276300a",
    "nonlinear-saturation": "sha256:010a928249a33ca398d7d6d80467898d63eb4d62ac0b2a66eba714a69faf61ca",
    "null-effect": "sha256:0eba789f5a97ea9b1457b44e24ae62fa44c8bbb494bfa066dd3cff1f4f19442d",
    "observational-confounding": "sha256:3d53045f12a3a5cdd4e424094e485a5edc52b994a722cbfb149cc3bb7a5cc735",
    "platform-interaction": "sha256:d776885c1b33b054967ed0f0c5243bb94ab497ef2f47ef157cf37a4c116f64f9",
    "sealed-holdout-reuse": "sha256:0f3551bece79a1e97d82f67573eac05d907ad8f21c8e94e4d916a53e41eeca36",
    "small-sample": "sha256:06513bd3d30161242cbe98af8abfc63cd29827158f2f8b5fb62cc0986678f0be",
    "structural-change-request": "sha256:2faed5d421ef270c359077671e6d13ca46401812a769f83ce0833fa6b8fc2871",
    "suppressed-missing": "sha256:06a6353cfdc10b6cb15154bccc1e0f2ebea6a1c38594e384915c3457cf43aee2",
    "tampered-hash": "sha256:94c113f968d697178829d9c1ac31e1ee09b62e72c584d77efd97bff101e9539f",
    "temporal-drift": "sha256:3e5dd2a62b61a7b876a7705aabb6f618064f1cc93c72be0811be6e82ff275c0c",
    "zero-inflated-value": "sha256:65603654a40bba8703928c1d7b280846459b821c0da69845a21109164e921527",
}
SYNTHETIC_SCENARIO_SEED = {
    "known-proof-need-miss": 2203,
    "non-identifiable-twin-a": 3303,
    "non-identifiable-twin-b": 3303,
    "null-effect": 1101,
    **{
        scenario_id: seed
        for scenario_id, (
            _dgp_id,
            _partition,
            seed,
        ) in _ADVERSARIAL_SCENARIO_SPECS.items()
    },
}

ALLOWED_PERSONA_FIELDS = frozenset({
    "anxieties",
    "decision_context",
    "motivations",
    "proof_needs",
    "role_context",
})
_STRING_PERSONA_FIELDS = frozenset({"decision_context", "role_context"})

_DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")
_SEMVER = re.compile(r"^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$")
_SYNTHETIC_RESPONSE_TIE_RULES = {"score-descending-creative-id-ascending"}
_PLATFORM_OBSERVATION_RULES = {
    "meta": {
        "primary": {
            "metric_id": "outbound_clicks",
            "denominator_kind": "impressions",
            "source_section": "delivery",
            "source_field": "impressions",
        },
        "rates": {},
    },
    "google": {
        "primary": {
            "metric_id": "conversions",
            "denominator_kind": "impressions",
            "source_section": "delivery",
            "source_field": "impressions",
        },
        "rates": {},
    },
    "linkedin": {
        "primary": {
            "metric_id": "total_conversions",
            "denominator_kind": "impressions",
            "source_section": "delivery",
            "source_field": "impressions",
        },
        "rates": {},
    },
    "tiktok": {
        "primary": {
            "metric_id": "cta_conversions",
            "denominator_kind": "impressions",
            "source_section": "delivery",
            "source_field": "impressions",
        },
        "rates": {
            "cvr-all-clicks": {
                "numerator_metric_id": "cta_conversions",
                "denominator_kind": "clicks_all",
                "source_section": "traffic",
                "source_field": "clicks_all",
            },
            "cvr-destination-click": {
                "numerator_metric_id": "cta_conversions",
                "denominator_kind": "destination_clicks",
                "source_section": "traffic",
                "source_field": "destination_clicks",
            },
            "cvr-impression": {
                "numerator_metric_id": "cta_conversions",
                "denominator_kind": "impressions",
                "source_section": "delivery",
                "source_field": "impressions",
            },
        },
    },
}
_PRIVATE_FIELD_DIGESTS = frozenset({
    "8f621532fbc911e20a19f302688d6943cc5bbab50bbc1f562206d19914221ce5",
    "9202af6ce925b26ae6b25adfff0b2705147e195fa38dd58ae6ecc58ed263751f",
    "f22636adad6a8611fb6d7ca98a8e84970fae776ca79595fe016c91e8c40a76ca",
    "8eaa36faa513563dd87fc40b213d72179fa027c01ba38392fb577e12a2ee203a",
    "d09c7835b0b4a1cb7b0378b60a21a923d0d42a4eb0f71eb2a61e091f9d343c86",
})
_EXPERIMENTAL_STATES = {
    "status": "experimental_only",
    "evidence_origin": "synthetic_fixture_only",
    "real_world_validation_status": "not_evaluated",
    "production_executable": False,
    "sandbox_candidate_materialization_permitted": True,
    "production_candidate_materialization_permitted": False,
    "activation_permitted": False,
    "active_panel_mutation_permitted": False,
}
_CANDIDATE_STATES = {
    "status": "sandbox_only",
    "evidence_origin": "synthetic_fixture_only",
    "real_world_validation_status": "not_evaluated",
    "registration_permitted": False,
    "activation_permitted": False,
    "active_panel_mutation_permitted": False,
}


def _copy(value: object, path: str = "$") -> object:
    """Copy JSON-shaped input and reject values canonical JSON cannot seal."""

    if isinstance(value, Mapping):
        copied: dict[str, object] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise ContractError(f"{path} object keys must be strings")
            if hashlib.sha256(key.encode("utf-8")).hexdigest() in _PRIVATE_FIELD_DIGESTS:
                raise ContractError(f"{path} contains a private-stage field")
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
    require_object(value, keys, path)
    copied = _copy(value, path)
    checked = require_object(copied, keys, path)
    return dict(checked)


def _digest(value: object, path: str) -> str:
    text = require_string(value, path)
    if not _DIGEST.fullmatch(text):
        raise ContractError(f"{path} must be a sha256: prefixed digest")
    return text


def _integer(value: object, path: str, *, minimum: int = 0) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise ContractError(f"{path} must be an integer >= {minimum}")
    return value


def _number(value: object, path: str, *, minimum: float | None = None) -> int | float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ContractError(f"{path} must be a finite number")
    if not math.isfinite(value) or (minimum is not None and value < minimum):
        raise ContractError(f"{path} must be a finite number")
    return value


def _semantic_version(value: object, path: str) -> str:
    text = require_string(value, path)
    if not _SEMVER.fullmatch(text):
        raise ContractError(
            f"{path} must be a semantic version in MAJOR.MINOR.PATCH form"
        )
    return text


def _metric_key(value: object, path: str) -> str:
    text = require_string(value, path)
    if not re.fullmatch(r"^[a-z][a-z0-9]*(?:[_-][a-z0-9]+)*$", text):
        raise ContractError(
            f"{path} must be a canonical lowercase metric name"
        )
    return text


def _persona_behavior_value(
    value: object,
    *,
    field: str,
    path: str,
) -> object:
    if field in _STRING_PERSONA_FIELDS:
        return require_string(value, path)
    return require_string_array(value, path, nonempty=True)


def _hashes(value: object, path: str, *, nonempty: bool = False) -> list[str]:
    values = require_array(value, path, nonempty=nonempty)
    result = [_digest(item, f"{path}[{index}]") for index, item in enumerate(values)]
    if len(result) != len(set(result)):
        raise ContractError(f"{path} must contain unique digests")
    if result != sorted(result):
        raise ContractError(f"{path} must be canonically sorted")
    return result


def _require_self_hash(document: dict[str, object], field: str, path: str) -> dict[str, object]:
    supplied = _digest(document[field], f"{path}.{field}")
    candidate = deepcopy(document)
    candidate[field] = None
    if supplied != sha256_json(candidate):
        raise ContractError(f"{path}.{field} does not match canonical content")
    return document


def _exact_constants(document: Mapping[str, object], constants: Mapping[str, object], path: str) -> None:
    for key, expected in constants.items():
        if type(document[key]) is not type(expected) or document[key] != expected:
            raise ContractError(f"{path}.{key} must be exactly {expected!r}")


def _binding(value: object, path: str) -> dict[str, object]:
    keys = {"panel_id", "panel_version", "panel_sha256", "persona_id", "persona_snapshot_sha256"}
    document = _object(value, keys, path)
    require_identifier(document["panel_id"], f"{path}.panel_id")
    require_string(document["panel_version"], f"{path}.panel_version")
    _digest(document["panel_sha256"], f"{path}.panel_sha256")
    require_identifier(document["persona_id"], f"{path}.persona_id")
    _digest(document["persona_snapshot_sha256"], f"{path}.persona_snapshot_sha256")
    return document


def validate_base_panel_binding_input(payload: object) -> dict[str, object]:
    """Validate the closed base-panel binding admitted to a private stage."""

    return _binding(payload, "base_panel_binding")


def _study_binding(value: object, path: str) -> dict[str, object]:
    document = _object(value, {"study_id", "study_manifest_sha256"}, path)
    require_identifier(document["study_id"], f"{path}.study_id")
    _digest(document["study_manifest_sha256"], f"{path}.study_manifest_sha256")
    return document


def _parameter_set(value: object, path: str) -> dict[str, object]:
    document = _object(
        value,
        {
            "parameter_set_id",
            "parameter_version",
            "parameter_values",
            "parameters_sha256",
        },
        path,
    )
    require_identifier(document["parameter_set_id"], f"{path}.parameter_set_id")
    _semantic_version(
        document["parameter_version"], f"{path}.parameter_version"
    )
    values = require_array(
        document["parameter_values"], f"{path}.parameter_values", nonempty=True
    )
    names: list[str] = []
    for index, raw_entry in enumerate(values):
        entry_path = f"{path}.parameter_values[{index}]"
        entry = _object(raw_entry, {"name", "value_type", "value"}, entry_path)
        name = require_string(entry["name"], f"{entry_path}.name")
        names.append(name)
        value_type = require_enum(
            entry["value_type"],
            {"integer", "number", "boolean", "string"},
            f"{entry_path}.value_type",
        )
        parameter_value = entry["value"]
        if value_type == "integer":
            if type(parameter_value) is not int:
                raise ContractError(f"{entry_path}.value must be an exact integer")
        elif value_type == "number":
            if type(parameter_value) is not float or not math.isfinite(parameter_value):
                raise ContractError(f"{entry_path}.value must be a finite number")
        elif value_type == "boolean":
            if type(parameter_value) is not bool:
                raise ContractError(f"{entry_path}.value must be an exact boolean")
        elif type(parameter_value) is not str or not parameter_value.strip():
            raise ContractError(f"{entry_path}.value must be a non-empty string")
    if len(names) != len(set(names)):
        raise ContractError(f"{path}.parameter_values must contain unique names")
    if names != sorted(names):
        raise ContractError(f"{path}.parameter_values must be canonically sorted")
    return _require_self_hash(document, "parameters_sha256", path)


def _validate_trusted_generator_observation_v1(
    document: dict[str, object],
) -> dict[str, object]:
    """Validate Task 2's compact projection for its trusted generator only.

    The generator projection predates raw-export normalization and remains
    byte-stable for its own fixture-replay contract. This private helper is
    never selected by caller-authored document shape.
    """

    closed_sections = {
        "source": {"platform"},
        "reporting_context": {
            "timezone", "currency", "report_time_basis", "maturity",
        },
        "entity_identity": {"account"},
        "experiment_binding": {
            "experiment", "campaign", "block", "batch", "arm", "reference_arm",
        },
        "creative_binding": {"creative"},
        "creative_attribute_binding": {"registry", "hypothesis"},
        "audience_scope": {"segment", "objective", "placement"},
        "delivery": {"impressions"},
        "traffic": {"clicks"},
        "outcome_events": {"conversions"},
        "measurement_definition": {
            "metric", "registered_numerator", "registered_denominator",
            "attribution_click_window", "attribution_view_window",
            "attribution_engaged_view_window", "attribution_model",
        },
        "denominators": {"kind"},
        "completeness": {"status"},
        "design_quality": {"design"},
    }
    for key, section_keys in closed_sections.items():
        section = _object(document[key], section_keys, f"observation.{key}")
        if key == "delivery":
            _number(section["impressions"], "observation.delivery.impressions", minimum=0)
        elif key == "traffic":
            _number(section["clicks"], "observation.traffic.clicks", minimum=0)
        elif key == "outcome_events":
            _number(section["conversions"], "observation.outcome_events.conversions", minimum=0)
        elif key == "completeness":
            require_enum(
                section["status"],
                {"finalized", "recent", "suppressed", "missing"},
                "observation.completeness.status",
            )
        elif key == "design_quality":
            require_enum(
                section["design"],
                {"randomized", "observational"},
                "observation.design_quality.design",
            )
        else:
            for value_key, value in section.items():
                require_string(value, f"observation.{key}.{value_key}")
    require_string_array(document["limitations"], "observation.limitations")
    return _require_self_hash(document, "observation_sha256", "observation")


def validate_outcome_observation(payload: object) -> dict[str, object]:
    keys = {
        "schema_version", "observation_id", "evidence_origin", "synthetic_study_binding",
        "source", "reporting_context", "entity_identity", "experiment_binding",
        "creative_binding", "creative_attribute_binding", "audience_scope", "delivery",
        "traffic", "outcome_events", "measurement_definition", "denominators",
        "completeness", "design_quality", "limitations", "observation_sha256",
    }
    document = _object(payload, keys, "observation")
    require_enum(document["schema_version"], {OUTCOME_OBSERVATION_VERSION}, "observation.schema_version")
    require_identifier(document["observation_id"], "observation.observation_id")
    require_enum(document["evidence_origin"], {"synthetic_fixture_only"}, "observation.evidence_origin")
    _study_binding(document["synthetic_study_binding"], "observation.synthetic_study_binding")
    source = _object(
        document["source"],
        {"platform", "source_sha256", "raw_schema_version", "source_scenario_id"},
        "observation.source",
    )
    require_enum(source["platform"], {"meta", "google", "linkedin", "tiktok"}, "observation.source.platform")
    _digest(source["source_sha256"], "observation.source.source_sha256")
    require_identifier(source["raw_schema_version"], "observation.source.raw_schema_version")
    require_identifier(source["source_scenario_id"], "observation.source.source_scenario_id")

    reporting = _object(
        document["reporting_context"],
        {
            "account_id", "timezone", "currency", "date", "grain",
            "report_time_basis", "maturity", "grouping_keys",
        },
        "observation.reporting_context",
    )
    for key in {"account_id", "timezone", "currency", "date", "grain", "report_time_basis"}:
        require_string(reporting[key], f"observation.reporting_context.{key}")
    require_enum(reporting["maturity"], {"finalized", "recent"}, "observation.reporting_context.maturity")
    grouping_keys = require_string_array(
        reporting["grouping_keys"],
        "observation.reporting_context.grouping_keys",
        nonempty=True,
    )
    if grouping_keys != sorted(grouping_keys):
        raise ContractError("observation.reporting_context.grouping_keys must be canonically sorted")

    identity = _object(
        document["entity_identity"],
        {"account_id", "campaign_id", "ad_group_id", "ad_id"},
        "observation.entity_identity",
    )
    for key, value in identity.items():
        require_string(value, f"observation.entity_identity.{key}")

    experiment = _object(
        document["experiment_binding"],
        {
            "experiment_id", "campaign_id", "block_id", "batch_id",
            "arm_id", "reference_arm_id",
        },
        "observation.experiment_binding",
    )
    for key, value in experiment.items():
        require_identifier(value, f"observation.experiment_binding.{key}")

    creative = _object(
        document["creative_binding"],
        {"creative_id", "asset_sha256"},
        "observation.creative_binding",
    )
    require_identifier(creative["creative_id"], "observation.creative_binding.creative_id")
    _digest(creative["asset_sha256"], "observation.creative_binding.asset_sha256")

    attribute_binding = _object(
        document["creative_attribute_binding"],
        {"registry_id", "registry_sha256", "hypothesis_ids", "attributes"},
        "observation.creative_attribute_binding",
    )
    require_identifier(attribute_binding["registry_id"], "observation.creative_attribute_binding.registry_id")
    _digest(attribute_binding["registry_sha256"], "observation.creative_attribute_binding.registry_sha256")
    hypothesis_ids = require_string_array(
        attribute_binding["hypothesis_ids"],
        "observation.creative_attribute_binding.hypothesis_ids",
    )
    if hypothesis_ids != sorted(hypothesis_ids):
        raise ContractError("observation.creative_attribute_binding.hypothesis_ids must be canonically sorted")
    attribute_rows = require_array(
        attribute_binding["attributes"],
        "observation.creative_attribute_binding.attributes",
        nonempty=True,
    )
    bound_attribute_ids: list[str] = []
    bound_hypothesis_ids: list[str] = []
    for index, raw_attribute in enumerate(attribute_rows):
        path = f"observation.creative_attribute_binding.attributes[{index}]"
        attribute = _object(
            raw_attribute,
            {
                "attribute_id", "attribute_version", "method_id", "value",
                "hypothesis_id",
            },
            path,
        )
        bound_attribute_ids.append(
            require_identifier(attribute["attribute_id"], f"{path}.attribute_id")
        )
        _semantic_version(attribute["attribute_version"], f"{path}.attribute_version")
        require_identifier(attribute["method_id"], f"{path}.method_id")
        _copy(attribute["value"], f"{path}.value")
        hypothesis_id = attribute["hypothesis_id"]
        if hypothesis_id is not None:
            bound_hypothesis_ids.append(
                require_identifier(hypothesis_id, f"{path}.hypothesis_id")
            )
    if bound_attribute_ids != sorted(bound_attribute_ids) or len(
        bound_attribute_ids
    ) != len(set(bound_attribute_ids)):
        raise ContractError(
            "observation.creative_attribute_binding.attributes must be "
            "unique and canonically sorted"
        )
    if sorted(bound_hypothesis_ids) != hypothesis_ids:
        raise ContractError(
            "observation.creative_attribute_binding.hypothesis_ids must "
            "equal hypotheses applicable to bound creative attributes"
        )

    audience = _object(
        document["audience_scope"],
        {"segment_id", "objective", "placement"},
        "observation.audience_scope",
    )
    for key, value in audience.items():
        require_identifier(value, f"observation.audience_scope.{key}")

    delivery = _object(
        document["delivery"],
        {
            "impressions", "spend", "spend_micros", "reach", "reach_status",
            "spend_local", "spend_usd", "sends", "sends_semantics",
            "video_metrics",
        },
        "observation.delivery",
    )
    _number(delivery["impressions"], "observation.delivery.impressions", minimum=0)
    for key in {
        "spend", "spend_micros", "spend_local", "spend_usd", "reach", "sends"
    }:
        value = delivery[key]
        if value is not None:
            _number(value, f"observation.delivery.{key}", minimum=0)
    if delivery["spend_micros"] is not None and type(delivery["spend_micros"]) is not int:
        raise ContractError("observation.delivery.spend_micros must be an exact integer or null")
    require_enum(
        delivery["reach_status"],
        {"not_reported", "non_additive_estimated"},
        "observation.delivery.reach_status",
    )
    require_enum(
        delivery["sends_semantics"],
        {"not_applicable", "sponsored-messaging-delivery"},
        "observation.delivery.sends_semantics",
    )
    video_metrics = _object(
        delivery["video_metrics"],
        {
            "video_p25", "video_p50", "video_p75", "video_p100",
            "video_watched_2s", "video_watched_6s",
        },
        "observation.delivery.video_metrics",
    )
    for key, value in video_metrics.items():
        if value is not None:
            _number(value, f"observation.delivery.video_metrics.{key}", minimum=0)

    traffic = _object(
        document["traffic"],
        {
            "clicks_all", "outbound_clicks", "other_clicks", "interactions",
            "destination_clicks", "chargeable_clicks", "landing_page_clicks",
        },
        "observation.traffic",
    )
    for key, value in traffic.items():
        if value is not None:
            _number(value, f"observation.traffic.{key}", minimum=0)

    events = require_array(document["outcome_events"], "observation.outcome_events", nonempty=True)
    event_ids: list[tuple[str, str]] = []
    event_values: dict[str, int | float | None] = {}
    event_states: dict[str, str] = {}
    event_kinds: dict[str, str] = {}
    for index, raw_event in enumerate(events):
        path = f"observation.outcome_events[{index}]"
        event = _object(
            raw_event,
            {
                "metric_id", "event_kind", "count", "value", "attribution_kind",
                "report_time_basis", "data_status",
            },
            path,
        )
        metric_id = _metric_key(event["metric_id"], f"{path}.metric_id")
        event_kind = require_enum(event["event_kind"], {"count", "action_value"}, f"{path}.event_kind")
        event_ids.append((metric_id, event_kind))
        for key in {"count", "value"}:
            value = event[key]
            if value is not None:
                _number(value, f"{path}.{key}", minimum=0)
        if event_kind == "count":
            if event["value"] is not None:
                raise ContractError(f"{path}.event_kind count requires value null")
            observed_value = event["count"]
        else:
            if event["count"] is not None:
                raise ContractError(
                    f"{path}.event_kind action_value requires count null"
                )
            observed_value = event["value"]
        require_enum(
            event["attribution_kind"],
            {"none", "aggregate", "cta", "vta", "evta", "post_click", "post_view"},
            f"{path}.attribution_kind",
        )
        require_string(event["report_time_basis"], f"{path}.report_time_basis")
        event_status = require_enum(
            event["data_status"],
            {"observed", "estimated", "modeled_and_observed", "suppressed", "missing", "omitted-zero", "zero"},
            f"{path}.data_status",
        )
        if event_status in {"observed", "estimated", "modeled_and_observed"}:
            if observed_value is None:
                raise ContractError(f"{path} observed data must have a non-null value")
        elif event_status == "zero":
            if observed_value != 0:
                raise ContractError(f"{path} zero data must have exact value zero")
        elif observed_value is not None:
            raise ContractError(f"{path} unavailable data must have a null value")
        if metric_id in event_values:
            raise ContractError(
                "observation.outcome_events must contain unique metric_id values"
            )
        event_values[metric_id] = observed_value
        event_states[metric_id] = event_status
        event_kinds[metric_id] = event_kind
    if len(event_ids) != len(set(event_ids)):
        raise ContractError("observation.outcome_events must not contain duplicate metric/event kinds")

    measurement = _object(
        document["measurement_definition"],
        {
            "primary_metric_id", "data_status", "attribution_model",
            "click_window", "view_window", "engaged_view_window",
            "interaction_date", "conversion_date", "third_party_event_date",
            "action_report_time", "attribution_report_time",
            "reporting_delay_days", "rates",
        },
        "observation.measurement_definition",
    )
    primary_metric_id = _metric_key(
        measurement["primary_metric_id"],
        "observation.measurement_definition.primary_metric_id",
    )
    platform = str(source["platform"])
    platform_rules = _PLATFORM_OBSERVATION_RULES[platform]
    primary_rule = platform_rules["primary"]
    if primary_metric_id != primary_rule["metric_id"]:
        raise ContractError(
            "observation.measurement_definition.primary_metric_id must "
            f"equal the approved primary metric for {platform}"
        )
    require_enum(
        measurement["data_status"],
        {"observed", "estimated", "modeled_and_observed"},
        "observation.measurement_definition.data_status",
    )
    for key in {
        "attribution_model", "click_window", "view_window", "engaged_view_window",
        "interaction_date", "conversion_date", "third_party_event_date",
        "action_report_time", "attribution_report_time",
    }:
        value = measurement[key]
        if value is not None:
            require_string(value, f"observation.measurement_definition.{key}")
    reporting_delay = measurement["reporting_delay_days"]
    if reporting_delay is not None:
        _integer(
            reporting_delay,
            "observation.measurement_definition.reporting_delay_days",
        )
    rates = require_array(
        measurement["rates"],
        "observation.measurement_definition.rates",
    )
    rate_rules = platform_rules["rates"]
    if rates and not rate_rules:
        raise ContractError(
            f"observation source platform {platform} does not allow rates"
        )
    rate_rows: dict[str, dict[str, object]] = {}
    for index, raw_rate in enumerate(rates):
        path = f"observation.measurement_definition.rates[{index}]"
        rate = _object(
            raw_rate,
            {
                "metric_id", "rate_value", "numerator_metric_id",
                "numerator_value", "denominator_kind", "denominator_value",
                "data_status",
            },
            path,
        )
        rate_id = _metric_key(rate["metric_id"], f"{path}.metric_id")
        if rate_id in rate_rows:
            raise ContractError("observation measurement rates must be unique")
        if rate_id not in rate_rules:
            raise ContractError(
                f"{path}.metric_id must be an approved rate for {platform}"
            )
        rate_rule = rate_rules[rate_id]
        rate_value = rate["rate_value"]
        if rate_value is not None:
            _number(rate_value, f"{path}.rate_value", minimum=0)
        numerator_id = _metric_key(
            rate["numerator_metric_id"],
            f"{path}.numerator_metric_id",
        )
        if numerator_id != rate_rule["numerator_metric_id"]:
            raise ContractError(
                f"{path}.numerator_metric_id must equal the approved numerator"
            )
        if event_kinds.get(numerator_id) != "count":
            raise ContractError(
                f"{path}.numerator_metric_id must reference a count event"
            )
        numerator_value = rate["numerator_value"]
        if numerator_value is not None:
            _number(
                numerator_value,
                f"{path}.numerator_value",
                minimum=0,
            )
        if (
            numerator_id not in event_values
            or type(event_values[numerator_id]) is not type(numerator_value)
            or event_values[numerator_id] != numerator_value
        ):
            raise ContractError(
                f"{path} must have a type-exact numerator equal to its "
                "emitted event"
            )
        denominator_kind = _metric_key(
            rate["denominator_kind"],
            f"{path}.denominator_kind",
        )
        if denominator_kind != rate_rule["denominator_kind"]:
            raise ContractError(
                f"{path}.denominator_kind must equal the approved denominator"
            )
        denominator_value = _number(
            rate["denominator_value"],
            f"{path}.denominator_value",
            minimum=0,
        )
        if denominator_value == 0:
            raise ContractError(
                f"{path}.denominator_value must be strictly positive"
            )
        source_values = (
            traffic
            if rate_rule["source_section"] == "traffic"
            else delivery
        )
        source_value = source_values[rate_rule["source_field"]]
        if (
            type(denominator_value) is not type(source_value)
            or denominator_value != source_value
        ):
            raise ContractError(
                f"{path} must have a type-exact denominator equal to its "
                "canonical source field"
            )
        rate_status = require_enum(
            rate["data_status"],
            {
                "observed", "estimated", "modeled_and_observed",
                "suppressed", "missing", "omitted-zero", "zero",
            },
            f"{path}.data_status",
        )
        if rate_status != event_states[numerator_id]:
            raise ContractError(
                f"{path}.data_status must equal the numerator event state"
            )
        if rate_status in {"observed", "estimated", "modeled_and_observed"}:
            if rate_value is None:
                raise ContractError(f"{path} observed rate must be non-null")
        elif rate_status == "zero":
            if rate_value != 0:
                raise ContractError(f"{path} zero rate must equal zero")
        elif rate_value is not None:
            raise ContractError(f"{path} unavailable rate must be null")
        if rate_value is not None:
            expected_rate = round(
                float(numerator_value) / float(denominator_value),
                8,
            )
            if rate_value != expected_rate:
                raise ContractError(
                    f"{path}.rate_value must equal the frozen rate "
                    "recomputation round(numerator / denominator, 8)"
                )
        rate_rows[rate_id] = rate
    if list(rate_rows) != sorted(rate_rows):
        raise ContractError(
            "observation.measurement_definition.rates must be canonically sorted"
        )
    if set(rate_rows) != set(rate_rules):
        raise ContractError(
            f"observation rates must equal the complete approved set for {platform}"
        )

    denominators = require_array(
        document["denominators"],
        "observation.denominators",
    )
    denominator_ids: list[str] = []
    denominator_rows: dict[str, dict[str, object]] = {}
    for index, raw_denominator in enumerate(denominators):
        path = f"observation.denominators[{index}]"
        denominator = _object(
            raw_denominator,
            {"metric_id", "denominator_kind", "denominator_value"},
            path,
        )
        metric_id = _metric_key(denominator["metric_id"], f"{path}.metric_id")
        denominator_ids.append(metric_id)
        _metric_key(denominator["denominator_kind"], f"{path}.denominator_kind")
        _number(denominator["denominator_value"], f"{path}.denominator_value", minimum=0)
        denominator_rows[metric_id] = denominator
    if len(denominator_ids) != len(set(denominator_ids)):
        raise ContractError("observation.denominators must contain unique metric_id values")
    for metric_id in denominator_ids:
        if metric_id not in event_values and metric_id not in rate_rows:
            raise ContractError(
                "observation denominator metric_id must reference an "
                "emitted metric or rate"
            )
    for rate_id in rate_rows:
        if rate_id not in denominator_rows:
            raise ContractError(
                f"observation rate {rate_id} must have exactly one denominator"
            )
    approved_denominator_ids = {primary_metric_id, *rate_rows}
    if set(denominator_rows) != approved_denominator_ids:
        raise ContractError(
            "observation denominators must equal the complete approved set "
            f"for {platform}"
        )

    primary_denominator = denominator_rows[primary_metric_id]
    primary_source = (
        traffic
        if primary_rule["source_section"] == "traffic"
        else delivery
    )[primary_rule["source_field"]]
    if primary_denominator["denominator_kind"] != primary_rule["denominator_kind"]:
        raise ContractError(
            "observation primary metric must use the approved primary "
            "denominator"
        )
    if (
        type(primary_denominator["denominator_value"]) is not type(primary_source)
        or primary_denominator["denominator_value"] != primary_source
    ):
        raise ContractError(
            "observation must have a type-exact primary denominator equal "
            "to its canonical source field"
        )

    for rate_id, rate in rate_rows.items():
        denominator = denominator_rows[rate_id]
        if (
            denominator["denominator_kind"] != rate["denominator_kind"]
            or type(denominator["denominator_value"])
            is not type(rate["denominator_value"])
            or denominator["denominator_value"] != rate["denominator_value"]
        ):
            raise ContractError(
                f"observation rate {rate_id} denominator must equal the "
                "emitted rate denominator"
            )

    completeness = _object(
        document["completeness"],
        {"metric_state", "row_state", "suppression_status", "omitted_zero_policy"},
        "observation.completeness",
    )
    for key in {"metric_state", "row_state"}:
        require_enum(
            completeness[key],
            {"observed", "zero", "missing", "suppressed", "omitted-zero"},
            f"observation.completeness.{key}",
        )
    if completeness["metric_state"] != completeness["row_state"]:
        raise ContractError(
            "observation completeness metric_state and row_state must match"
        )
    if primary_metric_id not in event_values:
        raise ContractError(
            "observation primary metric must exist in outcome_events"
        )
    primary_event = next(
        event
        for event in events
        if event["metric_id"] == primary_metric_id
    )
    if primary_event["data_status"] != completeness["metric_state"]:
        raise ContractError(
            "observation primary event state must match completeness"
        )
    if primary_metric_id not in denominator_ids:
        raise ContractError(
            "observation primary metric must have an explicit denominator"
        )
    suppression_status = require_enum(
        completeness["suppression_status"],
        {"not-suppressed", "suppressed-low-volume"},
        "observation.completeness.suppression_status",
    )
    if source["platform"] == "linkedin":
        expected_suppression = (
            "suppressed-low-volume"
            if completeness["metric_state"] == "suppressed"
            else "not-suppressed"
        )
        if suppression_status != expected_suppression:
            raise ContractError(
                "observation LinkedIn suppression_status must match "
                "completeness state"
            )
    require_enum(
        completeness["omitted_zero_policy"],
        {"explicit-metric-state"},
        "observation.completeness.omitted_zero_policy",
    )

    design = _object(
        document["design_quality"],
        {"design", "grouping_identity", "grouping_semantics", "overlap_permitted"},
        "observation.design_quality",
    )
    require_enum(design["design"], {"randomized", "observational"}, "observation.design_quality.design")
    require_identifier(design["grouping_identity"], "observation.design_quality.grouping_identity")
    require_enum(
        design["grouping_semantics"],
        {"mutually-exclusive-randomized-blocks"},
        "observation.design_quality.grouping_semantics",
    )
    if type(design["overlap_permitted"]) is not bool or design["overlap_permitted"]:
        raise ContractError("observation.design_quality.overlap_permitted must be exactly false")
    require_string_array(document["limitations"], "observation.limitations")
    return _require_self_hash(document, "observation_sha256", "observation")


def validate_study_manifest(payload: object) -> dict[str, object]:
    keys = {
        "schema_version", "study_id", "created_at", "purpose", "generator_version",
        "scenario_families", "estimands", "parameter_grid", "seeds", "repetitions",
        "monte_carlo_error_targets", "diagnosis_method", "synthetic_response_adapter",
        "stopping_rule", "performance_measures", "manifest_sha256",
    }
    document = _object(payload, keys, "study_manifest")
    require_enum(document["schema_version"], {STUDY_MANIFEST_VERSION}, "study_manifest.schema_version")
    require_identifier(document["study_id"], "study_manifest.study_id")
    require_timestamp(document["created_at"], "study_manifest.created_at")
    require_enum(document["purpose"], {"verification_only"}, "study_manifest.purpose")
    require_enum(
        document["generator_version"],
        {SYNTHETIC_STUDY_GENERATOR_VERSION},
        "study_manifest.generator_version",
    )
    families = require_array(document["scenario_families"], "study_manifest.scenario_families", nonempty=True)
    if len(families) != len(SYNTHETIC_SCENARIO_REGISTRY):
        raise ContractError(
            "study_manifest.scenario_families must contain every frozen scenario"
        )
    family_seeds: list[int] = []
    family_repetitions: list[int] = []
    for index, family in enumerate(families):
        member = _object(family, {"scenario_id", "dgp_id", "dgp_version", "seed", "repetitions", "parameters", "partition"}, f"study_manifest.scenario_families[{index}]")
        scenario_id = require_identifier(member["scenario_id"], f"study_manifest.scenario_families[{index}].scenario_id")
        expected = SYNTHETIC_SCENARIO_REGISTRY.get(scenario_id)
        if expected is None:
            raise ContractError(
                f"study_manifest.scenario_families[{index}].scenario_id is not frozen"
            )
        _exact_constants(
            member,
            expected,
            f"study_manifest.scenario_families[{index}]",
        )
        family_seeds.append(
            _integer(member["seed"], f"study_manifest.scenario_families[{index}].seed")
        )
        family_repetitions.append(
            _integer(
                member["repetitions"],
                f"study_manifest.scenario_families[{index}].repetitions",
                minimum=1,
            )
        )
        _parameter_set(
            member["parameters"],
            f"study_manifest.scenario_families[{index}].parameters",
        )
    ids = [str(item["scenario_id"]) for item in families]
    if len(ids) != len(set(ids)):
        raise ContractError("study_manifest.scenario_families must have unique scenario_id values")
    if set(ids) != set(SYNTHETIC_SCENARIO_REGISTRY):
        raise ContractError(
            "study_manifest.scenario_families must match the frozen scenario registry"
        )
    require_array(document["estimands"], "study_manifest.estimands", nonempty=True)
    performance_measures = require_string_array(
        document["performance_measures"],
        "study_manifest.performance_measures",
        nonempty=True,
    )
    if performance_measures != sorted(performance_measures):
        raise ContractError(
            "study_manifest.performance_measures must be canonically sorted"
        )
    grid = _object(document["parameter_grid"], {"rate"}, "study_manifest.parameter_grid")
    require_array(grid["rate"], "study_manifest.parameter_grid.rate", nonempty=True)
    seeds = require_array(document["seeds"], "study_manifest.seeds", nonempty=True)
    checked_seeds = [
        _integer(seed, f"study_manifest.seeds[{index}]")
        for index, seed in enumerate(seeds)
    ]
    expected_seeds = list(dict.fromkeys(family_seeds))
    if checked_seeds != expected_seeds:
        raise ContractError(
            "study_manifest.seeds must equal the ordered unique scenario-family seeds"
        )
    repetitions = _integer(
        document["repetitions"],
        "study_manifest.repetitions",
        minimum=1,
    )
    if any(value != repetitions for value in family_repetitions):
        raise ContractError(
            "study_manifest scenario-family repetitions must equal repetitions"
        )
    targets = _object(document["monte_carlo_error_targets"], {
        "maximum", "method_version", "batch_count", "batch_partition_policy",
        "quantile_interpolation", "reported_measures",
    }, "study_manifest.monte_carlo_error_targets")
    _number(targets["maximum"], "study_manifest.monte_carlo_error_targets.maximum", minimum=0)
    _exact_constants(targets, {
        "method_version": "deterministic-batch-quantile-mcse-v1",
        "batch_partition_policy": "equal_contiguous_replicate_batches",
        "quantile_interpolation": "linear",
    }, "study_manifest.monte_carlo_error_targets")
    batch_count = _integer(
        targets["batch_count"],
        "study_manifest.monte_carlo_error_targets.batch_count",
        minimum=2,
    )
    reported_measures = require_string_array(
        targets["reported_measures"],
        "study_manifest.monte_carlo_error_targets.reported_measures",
        nonempty=True,
    )
    if reported_measures != sorted(reported_measures):
        raise ContractError(
            "study_manifest.monte_carlo_error_targets.reported_measures "
            "must be canonically sorted"
        )
    if reported_measures != [
        "bootstrap_mean",
        "interval_lower",
        "interval_upper",
    ]:
        raise ContractError(
            "study_manifest.monte_carlo_error_targets.reported_measures "
            "must match the frozen measure set"
        )
    method = _object(document["diagnosis_method"], {
        "method_version", "contrast_source", "block_weighting", "experiment_weighting",
        "minimum_complete_blocks_per_experiment", "minimum_independent_experiments",
        "interval_method", "interval_level", "bootstrap_repetitions", "bootstrap_seed",
        "minimum_practical_effect", "minimum_practical_effect_rule",
        "missingness_policy", "maturity_policy",
        "observational_policy", "early_stopping_permitted",
    }, "study_manifest.diagnosis_method")
    _exact_constants(method, {
        "method_version": DIAGNOSIS_METHOD_VERSION,
        "contrast_source": "registered_numerator_denominator", "block_weighting": "equal",
        "experiment_weighting": "equal", "minimum_complete_blocks_per_experiment": 6,
        "minimum_independent_experiments": 2, "interval_method": "deterministic_percentile_block_bootstrap",
        "missingness_policy": "incomplete_block_ineligible", "maturity_policy": "finalized_only",
        "minimum_practical_effect_rule":
            "directional_point_estimate_strictly_exceeds_threshold",
        "observational_policy": "descriptive_only", "early_stopping_permitted": False,
    }, "study_manifest.diagnosis_method")
    interval_level = method["interval_level"]
    if (
        type(interval_level) is not float
        or not math.isfinite(interval_level)
        or not 0.0 < interval_level < 1.0
    ):
        raise ContractError(
            "study_manifest.diagnosis_method.interval_level must be an exact "
            "finite float strictly between zero and one"
        )
    _integer(method["bootstrap_repetitions"], "study_manifest.diagnosis_method.bootstrap_repetitions", minimum=1)
    _integer(method["bootstrap_seed"], "study_manifest.diagnosis_method.bootstrap_seed")
    _number(method["minimum_practical_effect"], "study_manifest.diagnosis_method.minimum_practical_effect", minimum=0)
    if method["bootstrap_repetitions"] % batch_count:
        raise ContractError(
            "study_manifest diagnosis bootstrap_repetitions must be divisible "
            "by monte_carlo_error_targets.batch_count"
        )
    adapter = _object(
        document["synthetic_response_adapter"],
        {
            "adapter_id",
            "version",
            "source_sha256",
            "feature_allowlist",
            "deterministic_tie_rule",
            "seed",
        },
        "study_manifest.synthetic_response_adapter",
    )
    require_identifier(adapter["adapter_id"], "study_manifest.synthetic_response_adapter.adapter_id")
    _semantic_version(
        adapter["version"], "study_manifest.synthetic_response_adapter.version"
    )
    _digest(
        adapter["source_sha256"],
        "study_manifest.synthetic_response_adapter.source_sha256",
    )
    features = require_string_array(
        adapter["feature_allowlist"],
        "study_manifest.synthetic_response_adapter.feature_allowlist",
        nonempty=True,
    )
    if features != sorted(features):
        raise ContractError(
            "study_manifest.synthetic_response_adapter.feature_allowlist "
            "must be canonically sorted"
        )
    require_enum(
        adapter["deterministic_tie_rule"],
        _SYNTHETIC_RESPONSE_TIE_RULES,
        "study_manifest.synthetic_response_adapter.deterministic_tie_rule",
    )
    _integer(adapter["seed"], "study_manifest.synthetic_response_adapter.seed")
    stopping = _object(document["stopping_rule"], {"rule"}, "study_manifest.stopping_rule")
    require_enum(stopping["rule"], {"none"}, "study_manifest.stopping_rule.rule")
    return _require_self_hash(document, "manifest_sha256", "study_manifest")


def validate_creative_attribute_registry(payload: object) -> dict[str, object]:
    keys = {"schema_version", "registry_id", "registered_at", "creative_bindings", "attribute_definitions", "creative_attributes", "annotation_methods", "review", "outcome_access_boundary", "registry_sha256"}
    document = _object(payload, keys, "attribute_registry")
    require_enum(document["schema_version"], {ATTRIBUTE_REGISTRY_VERSION}, "attribute_registry.schema_version")
    require_identifier(document["registry_id"], "attribute_registry.registry_id")
    registered_at = require_timestamp(document["registered_at"], "attribute_registry.registered_at")

    creative_bindings = require_array(
        document["creative_bindings"],
        "attribute_registry.creative_bindings",
        nonempty=True,
    )
    creatives: dict[str, str] = {}
    for index, raw_binding in enumerate(creative_bindings):
        path = f"attribute_registry.creative_bindings[{index}]"
        binding = _object(raw_binding, {"creative_id", "asset_sha256"}, path)
        creative_id = require_identifier(binding["creative_id"], f"{path}.creative_id")
        if creative_id in creatives:
            raise ContractError("attribute_registry.creative_bindings must contain unique creative_id values")
        creatives[creative_id] = _digest(binding["asset_sha256"], f"{path}.asset_sha256")
    if list(creatives) != sorted(creatives):
        raise ContractError("attribute_registry.creative_bindings must be canonically sorted")

    definitions = require_array(
        document["attribute_definitions"],
        "attribute_registry.attribute_definitions",
        nonempty=True,
    )
    attribute_definitions: dict[str, dict[str, object]] = {}
    hypothesis_ids: set[str] = set()
    for index, raw_definition in enumerate(definitions):
        path = f"attribute_registry.attribute_definitions[{index}]"
        definition = _object(
            raw_definition,
            {
                "attribute_id", "attribute_version", "attribute_kind",
                "value_type", "behavioral_hypothesis",
            },
            path,
        )
        attribute_id = require_identifier(definition["attribute_id"], f"{path}.attribute_id")
        if attribute_id in attribute_definitions:
            raise ContractError("attribute_registry.attribute_definitions must contain unique attribute_id values")
        _semantic_version(definition["attribute_version"], f"{path}.attribute_version")
        kind = require_enum(definition["attribute_kind"], {"objective", "interpretive"}, f"{path}.attribute_kind")
        require_enum(definition["value_type"], {"boolean", "number", "string", "string_array"}, f"{path}.value_type")
        hypothesis = definition["behavioral_hypothesis"]
        if hypothesis is not None:
            if kind != "interpretive":
                raise ContractError(f"{path}.behavioral_hypothesis is permitted only for interpretive attributes")
            checked_hypothesis = _object(
                hypothesis,
                {
                    "hypothesis_id", "target_persona_id", "target_persona_field",
                    "proposed_value", "rationale_template", "abstention_conditions",
                },
                f"{path}.behavioral_hypothesis",
            )
            hypothesis_id = require_identifier(
                checked_hypothesis["hypothesis_id"],
                f"{path}.behavioral_hypothesis.hypothesis_id",
            )
            if hypothesis_id in hypothesis_ids:
                raise ContractError("attribute_registry behavioral hypotheses must have unique hypothesis_id values")
            hypothesis_ids.add(hypothesis_id)
            require_identifier(
                checked_hypothesis["target_persona_id"],
                f"{path}.behavioral_hypothesis.target_persona_id",
            )
            require_enum(
                checked_hypothesis["target_persona_field"],
                set(ALLOWED_PERSONA_FIELDS),
                f"{path}.behavioral_hypothesis.target_persona_field allowed persona behavior fields",
            )
            proposed_value = checked_hypothesis["proposed_value"]
            if isinstance(proposed_value, str):
                require_string(proposed_value, f"{path}.behavioral_hypothesis.proposed_value")
            else:
                require_string_array(
                    proposed_value,
                    f"{path}.behavioral_hypothesis.proposed_value",
                    nonempty=True,
                )
            require_string(
                checked_hypothesis["rationale_template"],
                f"{path}.behavioral_hypothesis.rationale_template",
            )
            require_string_array(
                checked_hypothesis["abstention_conditions"],
                f"{path}.behavioral_hypothesis.abstention_conditions",
                nonempty=True,
            )
        attribute_definitions[attribute_id] = definition
    if list(attribute_definitions) != sorted(attribute_definitions):
        raise ContractError("attribute_registry.attribute_definitions must be canonically sorted")

    methods = require_array(
        document["annotation_methods"],
        "attribute_registry.annotation_methods",
        nonempty=True,
    )
    annotation_methods: dict[str, dict[str, object]] = {}
    for index, raw_method in enumerate(methods):
        path = f"attribute_registry.annotation_methods[{index}]"
        method = _object(
            raw_method,
            {"method_id", "method_version", "method_kind", "process_identity"},
            path,
        )
        method_id = require_identifier(method["method_id"], f"{path}.method_id")
        if method_id in annotation_methods:
            raise ContractError("attribute_registry.annotation_methods must contain unique method_id values")
        _semantic_version(method["method_version"], f"{path}.method_version")
        require_enum(method["method_kind"], {"deterministic", "human_review"}, f"{path}.method_kind")
        require_identifier(method["process_identity"], f"{path}.process_identity")
        annotation_methods[method_id] = method
    if list(annotation_methods) != sorted(annotation_methods):
        raise ContractError("attribute_registry.annotation_methods must be canonically sorted")

    attributes = require_array(
        document["creative_attributes"],
        "attribute_registry.creative_attributes",
        nonempty=True,
    )
    pairs: set[tuple[str, str]] = set()
    for index, raw_attribute in enumerate(attributes):
        path = f"attribute_registry.creative_attributes[{index}]"
        attribute = _object(
            raw_attribute,
            {
                "creative_id", "asset_sha256", "attribute_id",
                "attribute_version", "method_id", "value", "annotator",
                "confidence", "ambiguity", "review_status", "annotated_at",
            },
            path,
        )
        creative_id = require_identifier(attribute["creative_id"], f"{path}.creative_id")
        if creative_id not in creatives:
            raise ContractError(f"{path}.creative_id is not registered")
        asset_sha256 = _digest(attribute["asset_sha256"], f"{path}.asset_sha256")
        if asset_sha256 != creatives[creative_id]:
            raise ContractError(f"{path}.asset_sha256 does not match creative binding")
        attribute_id = require_identifier(attribute["attribute_id"], f"{path}.attribute_id")
        definition = attribute_definitions.get(attribute_id)
        if definition is None:
            raise ContractError(f"{path}.attribute_id is not defined")
        if attribute["attribute_version"] != definition["attribute_version"]:
            raise ContractError(f"{path}.attribute_version does not match definition")
        pair = (creative_id, attribute_id)
        if pair in pairs:
            raise ContractError("attribute_registry has a duplicate creative/attribute pair")
        pairs.add(pair)
        method_id = require_identifier(attribute["method_id"], f"{path}.method_id")
        method = annotation_methods.get(method_id)
        if method is None:
            raise ContractError(f"{path}.method_id is not registered")
        expected_method_kind = "deterministic" if definition["attribute_kind"] == "objective" else "human_review"
        if method["method_kind"] != expected_method_kind:
            raise ContractError(f"{path}.method_id has the wrong annotation method kind")
        value = attribute["value"]
        value_type = definition["value_type"]
        if value_type == "boolean" and type(value) is not bool:
            raise ContractError(f"{path}.value must be an exact boolean")
        if value_type == "number":
            _number(value, f"{path}.value")
        if value_type == "string":
            require_string(value, f"{path}.value")
        if value_type == "string_array":
            require_string_array(value, f"{path}.value", nonempty=True)
        annotator = require_identifier(attribute["annotator"], f"{path}.annotator")
        if annotator != method["process_identity"]:
            raise ContractError(f"{path}.annotator must match annotation method process_identity")
        confidence = attribute["confidence"]
        if (
            type(confidence) is not float
            or not math.isfinite(confidence)
            or not 0.0 <= confidence <= 1.0
        ):
            raise ContractError(f"{path}.confidence must be an exact float between zero and one")
        require_string(attribute["ambiguity"], f"{path}.ambiguity")
        require_enum(attribute["review_status"], {"approved"}, f"{path}.review_status")
        if require_timestamp(attribute["annotated_at"], f"{path}.annotated_at") > registered_at:
            raise ContractError(f"{path}.annotated_at must not be after registered_at")
    if [
        (item["creative_id"], item["attribute_id"]) for item in attributes
    ] != sorted(pairs):
        raise ContractError("attribute_registry.creative_attributes must be canonically sorted")

    review = _object(document["review"], {"status", "reviewed_by", "reviewed_at"}, "attribute_registry.review")
    require_enum(review["status"], {"approved"}, "attribute_registry.review.status")
    require_identifier(review["reviewed_by"], "attribute_registry.review.reviewed_by")
    reviewed_at = require_timestamp(review["reviewed_at"], "attribute_registry.review.reviewed_at")
    if reviewed_at > registered_at:
        raise ContractError("attribute_registry.review.reviewed_at must not be after registered_at")
    boundary = _object(
        document["outcome_access_boundary"],
        {"status", "earliest_outcome_accessed_at"},
        "attribute_registry.outcome_access_boundary",
    )
    require_enum(boundary["status"], {"pre_outcome"}, "attribute_registry.outcome_access_boundary.status")
    outcome_at = require_timestamp(
        boundary["earliest_outcome_accessed_at"],
        "attribute_registry.outcome_access_boundary.earliest_outcome_accessed_at",
    )
    if registered_at >= outcome_at:
        raise ContractError("creative attributes must be registered before outcome access")
    return _require_self_hash(document, "registry_sha256", "attribute_registry")


def validate_evidence_entry(payload: object) -> dict[str, object]:
    keys = {
        "schema_version", "entry_id", "observation", "observation_sha256",
        "source_sha256", "creative_attribute_registry_sha256",
        "study_manifest_sha256", "platform", "persona_ids", "segment_id",
        "objective", "placement", "experiment_id", "campaign_id", "block_id",
        "batch_id", "grouping_identity", "dependency_identity_sha256",
        "correction_identity_sha256",
        "design", "evidence_maturity", "metric_identity",
        "metric_identity_sha256", "denominator_kind", "attribution",
        "ingested_at", "provenance_state", "descriptive_claim_boundary",
        "supersedes_entry_id", "entry_sha256",
    }
    document = _object(payload, keys, "evidence_entry")
    require_enum(
        document["schema_version"],
        {EVIDENCE_ENTRY_VERSION},
        "evidence_entry.schema_version",
    )
    require_identifier(document["entry_id"], "evidence_entry.entry_id")
    observation = validate_outcome_observation(document["observation"])
    if observation["observation_id"] != document["entry_id"]:
        raise ContractError(
            "evidence_entry.entry_id must equal observation.observation_id"
        )
    for field in (
        "observation_sha256", "source_sha256",
        "creative_attribute_registry_sha256", "study_manifest_sha256",
        "dependency_identity_sha256", "correction_identity_sha256",
        "metric_identity_sha256",
    ):
        _digest(document[field], f"evidence_entry.{field}")
    if document["observation_sha256"] != observation["observation_sha256"]:
        raise ContractError(
            "evidence_entry.observation_sha256 must equal the observation hash"
        )
    if document["source_sha256"] != observation["source"]["source_sha256"]:
        raise ContractError(
            "evidence_entry.source_sha256 must equal the source binding"
        )
    if (
        document["creative_attribute_registry_sha256"]
        != observation["creative_attribute_binding"]["registry_sha256"]
    ):
        raise ContractError(
            "evidence_entry creative attribute registry binding is inconsistent"
        )
    if (
        document["study_manifest_sha256"]
        != observation["synthetic_study_binding"]["study_manifest_sha256"]
    ):
        raise ContractError(
            "evidence_entry study manifest binding is inconsistent"
        )
    require_enum(
        document["platform"],
        {"meta", "google", "linkedin", "tiktok"},
        "evidence_entry.platform",
    )
    if document["platform"] != observation["source"]["platform"]:
        raise ContractError("evidence_entry platform binding is inconsistent")
    persona_ids = require_string_array(
        document["persona_ids"], "evidence_entry.persona_ids"
    )
    for index, persona_id in enumerate(persona_ids):
        require_identifier(persona_id, f"evidence_entry.persona_ids[{index}]")
    if persona_ids != sorted(persona_ids):
        raise ContractError(
            "evidence_entry.persona_ids must be canonically sorted"
        )
    for field in (
        "segment_id", "objective", "placement", "experiment_id", "campaign_id",
        "block_id", "batch_id", "grouping_identity", "denominator_kind",
    ):
        require_identifier(document[field], f"evidence_entry.{field}")
    for field in ("segment_id", "objective", "placement"):
        if document[field] != observation["audience_scope"][field]:
            raise ContractError(f"evidence_entry.{field} binding is inconsistent")
    for field in ("experiment_id", "campaign_id", "block_id", "batch_id"):
        if document[field] != observation["experiment_binding"][field]:
            raise ContractError(f"evidence_entry.{field} binding is inconsistent")
    if (
        document["grouping_identity"]
        != observation["design_quality"]["grouping_identity"]
    ):
        raise ContractError(
            "evidence_entry.grouping_identity binding is inconsistent"
        )
    if (
        document["correction_identity_sha256"]
        != evidence_correction_identity_sha256(observation)
    ):
        raise ContractError(
            "evidence_entry.correction_identity_sha256 does not match "
            "the immutable analytical row"
        )
    require_enum(
        document["design"],
        {"randomized", "observational"},
        "evidence_entry.design",
    )
    if document["design"] != observation["design_quality"]["design"]:
        raise ContractError("evidence_entry.design binding is inconsistent")
    require_enum(
        document["evidence_maturity"],
        {"finalized", "recent"},
        "evidence_entry.evidence_maturity",
    )
    if document["evidence_maturity"] != observation["reporting_context"]["maturity"]:
        raise ContractError(
            "evidence_entry.evidence_maturity binding is inconsistent"
        )
    metric = _object(
        document["metric_identity"],
        {
            "metric_id", "event_kind", "direction", "denominator_kind",
            "attribution_model", "click_window", "view_window",
            "engaged_view_window", "report_time_basis", "data_status",
            "currency", "timezone",
        },
        "evidence_entry.metric_identity",
    )
    _metric_key(metric["metric_id"], "evidence_entry.metric_identity.metric_id")
    require_enum(
        metric["event_kind"],
        {"count", "action_value"},
        "evidence_entry.metric_identity.event_kind",
    )
    require_enum(
        metric["direction"],
        {"higher_is_better"},
        "evidence_entry.metric_identity.direction",
    )
    require_identifier(
        metric["denominator_kind"],
        "evidence_entry.metric_identity.denominator_kind",
    )
    for field in (
        "attribution_model", "click_window", "view_window",
        "engaged_view_window", "report_time_basis", "data_status",
        "currency", "timezone",
    ):
        if metric[field] is not None:
            require_string(
                metric[field], f"evidence_entry.metric_identity.{field}"
            )
    if document["metric_identity_sha256"] != sha256_json(metric):
        raise ContractError(
            "evidence_entry.metric_identity_sha256 does not match canonical content"
        )
    if document["denominator_kind"] != metric["denominator_kind"]:
        raise ContractError(
            "evidence_entry.denominator_kind binding is inconsistent"
        )
    attribution = _object(
        document["attribution"],
        {
            "model", "click_window", "view_window", "engaged_view_window",
            "report_time_basis",
        },
        "evidence_entry.attribution",
    )
    for field, metric_field in (
        ("model", "attribution_model"),
        ("click_window", "click_window"),
        ("view_window", "view_window"),
        ("engaged_view_window", "engaged_view_window"),
        ("report_time_basis", "report_time_basis"),
    ):
        if attribution[field] is not None:
            require_string(attribution[field], f"evidence_entry.attribution.{field}")
        if attribution[field] != metric[metric_field]:
            raise ContractError(
                f"evidence_entry.attribution.{field} binding is inconsistent"
            )
    require_timestamp(document["ingested_at"], "evidence_entry.ingested_at")
    require_enum(
        document["provenance_state"],
        {"synthetic_fixture_only"},
        "evidence_entry.provenance_state",
    )
    require_enum(
        document["descriptive_claim_boundary"],
        {"associated_with_outcome"},
        "evidence_entry.descriptive_claim_boundary",
    )
    if document["supersedes_entry_id"] is not None:
        require_identifier(
            document["supersedes_entry_id"],
            "evidence_entry.supersedes_entry_id",
        )
        if document["supersedes_entry_id"] == document["entry_id"]:
            raise ContractError("evidence_entry cannot supersede itself")
    return _require_self_hash(document, "entry_sha256", "evidence_entry")


def evidence_correction_identity_sha256(
    observation: Mapping[str, object],
) -> str:
    """Seal the analytical row while excluding replaceable outcome bytes."""

    source = observation["source"]
    measurement = observation["measurement_definition"]
    assert isinstance(source, Mapping)
    assert isinstance(measurement, Mapping)
    rate_identity = [
        {
            "metric_id": row["metric_id"],
            "numerator_metric_id": row["numerator_metric_id"],
            "denominator_kind": row["denominator_kind"],
            "data_status": row["data_status"],
        }
        for row in measurement["rates"]
    ]
    measurement_identity = {
        key: value
        for key, value in measurement.items()
        if key != "rates"
    }
    measurement_identity["rates"] = rate_identity
    denominator_identity = [
        {
            "metric_id": row["metric_id"],
            "denominator_kind": row["denominator_kind"],
        }
        for row in observation["denominators"]
    ]
    event_identity = [
        {
            "metric_id": row["metric_id"],
            "event_kind": row["event_kind"],
            "attribution_kind": row["attribution_kind"],
            "report_time_basis": row["report_time_basis"],
            "data_status": row["data_status"],
        }
        for row in observation["outcome_events"]
    ]
    reporting = observation["reporting_context"]
    experiment = observation["experiment_binding"]
    creative = observation["creative_binding"]
    quality = observation["design_quality"]
    dependency_identity = {
        "platform": source["platform"],
        "account_id": reporting["account_id"],
        "experiment_id": experiment["experiment_id"],
        "campaign_id": experiment["campaign_id"],
        "block_id": experiment["block_id"],
        "batch_id": experiment["batch_id"],
        "arm_id": experiment["arm_id"],
        "creative_id": creative["creative_id"],
        "grouping_identity": quality["grouping_identity"],
    }
    identity = {
        "evidence_origin": observation["evidence_origin"],
        "synthetic_study_binding": observation["synthetic_study_binding"],
        "source_identity": {
            "platform": source["platform"],
            "raw_schema_version": source["raw_schema_version"],
            "source_scenario_id": source["source_scenario_id"],
        },
        "reporting_context": observation["reporting_context"],
        "entity_identity": observation["entity_identity"],
        "experiment_binding": observation["experiment_binding"],
        "creative_binding": observation["creative_binding"],
        "creative_attribute_binding": observation[
            "creative_attribute_binding"
        ],
        "audience_scope": observation["audience_scope"],
        "measurement_identity": measurement_identity,
        "denominator_identity": denominator_identity,
        "event_identity": event_identity,
        "completeness": observation["completeness"],
        "design_quality": observation["design_quality"],
        "source_row_dependency_identity_sha256": sha256_json(
            dependency_identity
        ),
    }
    return sha256_json(identity)


def validate_evidence_event(payload: object) -> dict[str, object]:
    keys = {
        "schema_version", "event_id", "effective_at", "operation", "entry_id",
        "entry_sha256", "superseded_entry_id", "superseded_entry_sha256",
        "correction_reason", "previous_event_sha256", "event_sha256",
    }
    document = _object(payload, keys, "evidence_event")
    require_enum(
        document["schema_version"],
        {EVIDENCE_EVENT_VERSION},
        "evidence_event.schema_version",
    )
    require_identifier(document["event_id"], "evidence_event.event_id")
    require_timestamp(document["effective_at"], "evidence_event.effective_at")
    operation = require_enum(
        document["operation"],
        {"append", "correct"},
        "evidence_event.operation",
    )
    require_identifier(document["entry_id"], "evidence_event.entry_id")
    _digest(document["entry_sha256"], "evidence_event.entry_sha256")
    if document["previous_event_sha256"] is not None:
        _digest(
            document["previous_event_sha256"],
            "evidence_event.previous_event_sha256",
        )
    correction_values = (
        document["superseded_entry_id"],
        document["superseded_entry_sha256"],
        document["correction_reason"],
    )
    if operation == "append":
        if correction_values != (None, None, None):
            raise ContractError(
                "append evidence events must have null correction fields"
            )
    else:
        require_identifier(
            document["superseded_entry_id"],
            "evidence_event.superseded_entry_id",
        )
        _digest(
            document["superseded_entry_sha256"],
            "evidence_event.superseded_entry_sha256",
        )
        require_string(
            document["correction_reason"],
            "evidence_event.correction_reason",
        )
        if document["superseded_entry_id"] == document["entry_id"]:
            raise ContractError("a correction event cannot supersede itself")
    return _require_self_hash(document, "event_sha256", "evidence_event")


def validate_evidence_receipt(payload: object) -> dict[str, object]:
    keys = {
        "schema_version", "receipt_id", "library_id", "effective_at",
        "event_count", "event_id", "event_sha256", "projection_sha256",
        "receipt_sha256",
    }
    document = _object(payload, keys, "evidence_receipt")
    require_enum(
        document["schema_version"],
        {EVIDENCE_RECEIPT_VERSION},
        "evidence_receipt.schema_version",
    )
    require_identifier(document["receipt_id"], "evidence_receipt.receipt_id")
    require_identifier(document["library_id"], "evidence_receipt.library_id")
    require_timestamp(document["effective_at"], "evidence_receipt.effective_at")
    count = _integer(
        document["event_count"], "evidence_receipt.event_count", minimum=1
    )
    require_identifier(document["event_id"], "evidence_receipt.event_id")
    if document["receipt_id"] != document["event_id"]:
        raise ContractError(
            "evidence_receipt.receipt_id must equal event_id"
        )
    _digest(document["event_sha256"], "evidence_receipt.event_sha256")
    _digest(document["projection_sha256"], "evidence_receipt.projection_sha256")
    if count < 1:
        raise ContractError("evidence_receipt.event_count must be positive")
    return _require_self_hash(document, "receipt_sha256", "evidence_receipt")


def validate_evidence_library_index(payload: object) -> dict[str, object]:
    keys = {
        "schema_version", "library_id", "created_at", "updated_at",
        "event_count", "event_head_sha256", "entry_ids", "active_entry_ids",
        "source_sha256", "observation_sha256", "dependency_identity_sha256",
        "head_receipt_sha256", "library_sha256",
    }
    document = _object(payload, keys, "evidence_library_index")
    require_enum(
        document["schema_version"],
        {EVIDENCE_LIBRARY_INDEX_VERSION},
        "evidence_library_index.schema_version",
    )
    require_identifier(
        document["library_id"], "evidence_library_index.library_id"
    )
    created = require_timestamp(
        document["created_at"], "evidence_library_index.created_at"
    )
    count = _integer(
        document["event_count"],
        "evidence_library_index.event_count",
        minimum=0,
    )
    if document["updated_at"] is not None:
        updated = require_timestamp(
            document["updated_at"], "evidence_library_index.updated_at"
        )
        if updated < created:
            raise ContractError(
                "evidence_library_index.updated_at must not precede created_at"
            )
    for field in ("entry_ids", "active_entry_ids"):
        values = require_string_array(
            document[field], f"evidence_library_index.{field}"
        )
        for index, value in enumerate(values):
            require_identifier(
                value, f"evidence_library_index.{field}[{index}]"
            )
        if field == "active_entry_ids" and values != sorted(values):
            raise ContractError(
                f"evidence_library_index.{field} must be canonically sorted"
            )
    for field in (
        "source_sha256", "observation_sha256", "dependency_identity_sha256"
    ):
        _hashes(document[field], f"evidence_library_index.{field}")
    if count == 0:
        for field in (
            "updated_at", "event_head_sha256", "head_receipt_sha256"
        ):
            if document[field] is not None:
                raise ContractError(
                    f"empty evidence_library_index.{field} must be null"
                )
        if any(
            document[field]
            for field in (
                "entry_ids", "active_entry_ids", "source_sha256",
                "observation_sha256", "dependency_identity_sha256",
            )
        ):
            raise ContractError("empty evidence library index must have no entries")
    else:
        if document["updated_at"] is None:
            raise ContractError(
                "non-empty evidence_library_index.updated_at must be present"
            )
        _digest(
            document["event_head_sha256"],
            "evidence_library_index.event_head_sha256",
        )
        _digest(
            document["head_receipt_sha256"],
            "evidence_library_index.head_receipt_sha256",
        )
        if count != len(document["entry_ids"]):
            raise ContractError(
                "evidence_library_index event count must equal immutable entry count"
            )
    if not set(document["active_entry_ids"]).issubset(set(document["entry_ids"])):
        raise ContractError(
            "evidence_library_index.active_entry_ids must be registered entries"
        )
    return _require_self_hash(document, "library_sha256", "evidence_library_index")


def validate_evidence_library(payload: object) -> dict[str, object]:
    keys = {
        "schema_version", "library_id", "as_of", "created_at", "entry_ids",
        "entries", "historical_entry_ids", "historical_entries",
        "events", "event_count", "head_receipt", "library_sha256",
    }
    document = _object(payload, keys, "evidence_library")
    require_enum(
        document["schema_version"],
        {EVIDENCE_LIBRARY_VERSION},
        "evidence_library.schema_version",
    )
    require_identifier(document["library_id"], "evidence_library.library_id")
    as_of = require_timestamp(document["as_of"], "evidence_library.as_of")
    created = require_timestamp(
        document["created_at"], "evidence_library.created_at"
    )
    if as_of < created:
        raise ContractError("evidence_library.as_of must not precede created_at")
    entry_ids = require_string_array(
        document["entry_ids"], "evidence_library.entry_ids"
    )
    if entry_ids != sorted(entry_ids):
        raise ContractError(
            "evidence_library.entry_ids must be canonically sorted"
        )
    entries = [
        validate_evidence_entry(value)
        for value in require_array(document["entries"], "evidence_library.entries")
    ]
    if [entry["entry_id"] for entry in entries] != entry_ids:
        raise ContractError(
            "evidence_library.entries must exactly match canonical entry_ids"
        )
    historical_entry_ids = require_string_array(
        document["historical_entry_ids"],
        "evidence_library.historical_entry_ids",
    )
    if len(historical_entry_ids) != len(set(historical_entry_ids)):
        raise ContractError(
            "evidence_library.historical_entry_ids must be unique"
        )
    historical_entries = [
        validate_evidence_entry(value)
        for value in require_array(
            document["historical_entries"],
            "evidence_library.historical_entries",
        )
    ]
    if [
        entry["entry_id"] for entry in historical_entries
    ] != historical_entry_ids:
        raise ContractError(
            "evidence_library historical entries must exactly match their IDs"
        )
    events = [
        validate_evidence_event(value)
        for value in require_array(document["events"], "evidence_library.events")
    ]
    count = _integer(
        document["event_count"], "evidence_library.event_count", minimum=0
    )
    if count != len(events):
        raise ContractError(
            "evidence_library.event_count must equal the event array length"
        )
    if [str(event["entry_id"]) for event in events] != historical_entry_ids:
        raise ContractError(
            "evidence_library historical entries must exactly match event order"
        )
    history_by_id = {
        str(entry["entry_id"]): entry for entry in historical_entries
    }
    active: dict[str, str] = {}
    previous_event_sha256: str | None = None
    previous_effective_at = created
    for index, event in enumerate(events):
        event_path = f"evidence_library.events[{index}]"
        effective_at = require_timestamp(
            event["effective_at"], f"{event_path}.effective_at"
        )
        if effective_at < previous_effective_at or effective_at > as_of:
            raise ContractError(
                "evidence_library events must be chronological and not exceed as_of"
            )
        if event["previous_event_sha256"] != previous_event_sha256:
            raise ContractError(
                "evidence_library event chain is not contiguous"
            )
        entry_id = str(event["entry_id"])
        entry_sha256 = str(event["entry_sha256"])
        historical_entry = history_by_id[entry_id]
        if (
            historical_entry["entry_sha256"] != entry_sha256
            or historical_entry["ingested_at"] != event["effective_at"]
        ):
            raise ContractError(
                "evidence_library event does not bind its immutable historical entry"
            )
        if entry_id in active:
            raise ContractError(
                "evidence_library cannot append an already-active entry"
            )
        if event["operation"] == "correct":
            superseded_id = str(event["superseded_entry_id"])
            if active.get(superseded_id) != event["superseded_entry_sha256"]:
                raise ContractError(
                    "evidence_library correction must supersede the exact active entry"
                )
            superseded_entry = history_by_id.get(superseded_id)
            if (
                superseded_entry is None
                or superseded_entry["entry_sha256"]
                != event["superseded_entry_sha256"]
                or historical_entry["supersedes_entry_id"] != superseded_id
            ):
                raise ContractError(
                    "evidence_library correction does not bind both immutable entries"
                )
            del active[superseded_id]
        elif historical_entry["supersedes_entry_id"] is not None:
            raise ContractError(
                "evidence_library append entry cannot carry correction provenance"
            )
        active[entry_id] = entry_sha256
        previous_event_sha256 = str(event["event_sha256"])
        previous_effective_at = effective_at
    if sorted(active) != entry_ids:
        raise ContractError(
            "evidence_library active entry IDs do not replay from its event chain"
        )
    for entry in entries:
        if active.get(str(entry["entry_id"])) != entry["entry_sha256"]:
            raise ContractError(
                "evidence_library active entry bytes do not match their event"
            )
    if count == 0:
        if document["head_receipt"] is not None:
            raise ContractError(
                "empty evidence_library.head_receipt must be null"
            )
    else:
        receipt = validate_evidence_receipt(document["head_receipt"])
        if receipt["library_id"] != document["library_id"]:
            raise ContractError(
                "evidence_library head receipt library binding is inconsistent"
            )
        if receipt["event_count"] != count:
            raise ContractError(
                "evidence_library head receipt count is inconsistent"
            )
        if receipt["event_sha256"] != events[-1]["event_sha256"]:
            raise ContractError(
                "evidence_library head receipt event is inconsistent"
            )
        if (
            receipt["event_id"] != events[-1]["event_id"]
            or receipt["effective_at"] != events[-1]["effective_at"]
        ):
            raise ContractError(
                "evidence_library head receipt identity is inconsistent"
            )
    return _require_self_hash(document, "library_sha256", "evidence_library")


def validate_diagnosis(payload: object) -> dict[str, object]:
    keys = {
        "schema_version", "diagnosis_id", "diagnosed_at", "decision",
        "base_panel_binding", "base_panel_authority_status",
        "synthetic_study_binding",
        "evidence_library_projection_binding",
        "evidence_head_receipt_binding", "frozen_analysis_bindings",
        "target_persona_id", "eligible_hypothesis_ids",
        "selected_hypothesis", "analysis", "alternative_causes",
        "limitations", "diagnosis_sha256",
    }
    document = _object(payload, keys, "diagnosis")
    require_enum(document["schema_version"], {DIAGNOSIS_VERSION}, "diagnosis.schema_version")
    require_identifier(document["diagnosis_id"], "diagnosis.diagnosis_id")
    require_timestamp(document["diagnosed_at"], "diagnosis.diagnosed_at")
    require_enum(document["decision"], {"repeatable_behavioral_miss", "no_repeatable_miss", "non_identifiable", "alternative_cause_not_cleared", "insufficient_evidence", "invalid_evidence"}, "diagnosis.decision")
    _binding(document["base_panel_binding"], "diagnosis.base_panel_binding")
    require_enum(
        document["base_panel_authority_status"],
        {"unverified_proposal_context"},
        "diagnosis.base_panel_authority_status",
    )
    _study_binding(
        document["synthetic_study_binding"],
        "diagnosis.synthetic_study_binding",
    )
    projection = _object(
        document["evidence_library_projection_binding"],
        {"library_id", "as_of", "library_sha256", "projection_sha256"},
        "diagnosis.evidence_library_projection_binding",
    )
    require_identifier(
        projection["library_id"],
        "diagnosis.evidence_library_projection_binding.library_id",
    )
    require_timestamp(
        projection["as_of"],
        "diagnosis.evidence_library_projection_binding.as_of",
    )
    _digest(
        projection["library_sha256"],
        "diagnosis.evidence_library_projection_binding.library_sha256",
    )
    _digest(
        projection["projection_sha256"],
        "diagnosis.evidence_library_projection_binding.projection_sha256",
    )
    receipt = _object(
        document["evidence_head_receipt_binding"],
        {
            "receipt_id", "receipt_sha256", "event_count",
            "event_sha256", "projection_sha256",
        },
        "diagnosis.evidence_head_receipt_binding",
    )
    require_identifier(
        receipt["receipt_id"],
        "diagnosis.evidence_head_receipt_binding.receipt_id",
    )
    _integer(
        receipt["event_count"],
        "diagnosis.evidence_head_receipt_binding.event_count",
        minimum=1,
    )
    for field in ("receipt_sha256", "event_sha256", "projection_sha256"):
        _digest(
            receipt[field],
            f"diagnosis.evidence_head_receipt_binding.{field}",
        )
    if receipt["projection_sha256"] != projection["projection_sha256"]:
        raise ContractError(
            "diagnosis evidence projection and receipt hashes must match"
        )
    frozen = _object(
        document["frozen_analysis_bindings"],
        {
            "diagnosis_method_version", "diagnosis_method_sha256",
            "monte_carlo_error_method_version",
            "monte_carlo_error_method_sha256", "estimand_sha256",
            "stopping_rule_sha256", "experiment_design_sha256",
            "creative_attribute_registry_sha256",
        },
        "diagnosis.frozen_analysis_bindings",
    )
    require_enum(
        frozen["diagnosis_method_version"],
        {DIAGNOSIS_METHOD_VERSION},
        "diagnosis.frozen_analysis_bindings.diagnosis_method_version",
    )
    require_enum(
        frozen["monte_carlo_error_method_version"],
        {"deterministic-batch-quantile-mcse-v1"},
        "diagnosis.frozen_analysis_bindings.monte_carlo_error_method_version",
    )
    for field in (
        "diagnosis_method_sha256", "monte_carlo_error_method_sha256",
        "estimand_sha256", "stopping_rule_sha256",
        "creative_attribute_registry_sha256",
    ):
        _digest(frozen[field], f"diagnosis.frozen_analysis_bindings.{field}")
    _hashes(
        frozen["experiment_design_sha256"],
        "diagnosis.frozen_analysis_bindings.experiment_design_sha256",
        nonempty=True,
    )
    if document["target_persona_id"] is not None:
        require_identifier(
            document["target_persona_id"], "diagnosis.target_persona_id"
        )
    eligible = require_string_array(
        document["eligible_hypothesis_ids"],
        "diagnosis.eligible_hypothesis_ids",
    )
    for index, hypothesis_id in enumerate(eligible):
        require_identifier(
            hypothesis_id,
            f"diagnosis.eligible_hypothesis_ids[{index}]",
        )
    if eligible != sorted(eligible) or len(eligible) != len(set(eligible)):
        raise ContractError(
            "diagnosis.eligible_hypothesis_ids must be unique and canonically sorted"
        )
    if document["selected_hypothesis"] is not None:
        selected = _object(
            document["selected_hypothesis"],
            {
                "hypothesis_id", "attribute_id", "target_persona_id",
                "target_persona_field", "proposed_value",
                "rationale_template", "evidence_entry_ids",
                "evidence_sha256",
            },
            "diagnosis.selected_hypothesis",
        )
        for field in ("hypothesis_id", "attribute_id", "target_persona_id"):
            require_identifier(
                selected[field], f"diagnosis.selected_hypothesis.{field}"
            )
        require_enum(
            selected["target_persona_field"],
            ALLOWED_PERSONA_FIELDS,
            "diagnosis.selected_hypothesis.target_persona_field",
        )
        _copy(
            selected["proposed_value"],
            "diagnosis.selected_hypothesis.proposed_value",
        )
        require_string(
            selected["rationale_template"],
            "diagnosis.selected_hypothesis.rationale_template",
        )
        evidence_ids = require_string_array(
            selected["evidence_entry_ids"],
            "diagnosis.selected_hypothesis.evidence_entry_ids",
            nonempty=True,
        )
        for index, entry_id in enumerate(evidence_ids):
            require_identifier(
                entry_id,
                f"diagnosis.selected_hypothesis.evidence_entry_ids[{index}]",
            )
        if evidence_ids != sorted(evidence_ids):
            raise ContractError(
                "diagnosis selected evidence IDs must be canonically sorted"
            )
        _hashes(
            selected["evidence_sha256"],
            "diagnosis.selected_hypothesis.evidence_sha256",
            nonempty=True,
        )
        if selected["hypothesis_id"] not in eligible:
            raise ContractError(
                "diagnosis selected hypothesis must be eligible"
            )
        if selected["target_persona_id"] != document["target_persona_id"]:
            raise ContractError(
                "diagnosis selected hypothesis persona is inconsistent"
            )
    elif document["decision"] == "repeatable_behavioral_miss":
        raise ContractError(
            "repeatable diagnosis must select one hypothesis"
        )
    if document["decision"] == "repeatable_behavioral_miss":
        if len(eligible) != 1:
            raise ContractError(
                "repeatable diagnosis must have exactly one eligible hypothesis"
            )
    elif eligible:
        raise ContractError(
            "non-repeatable diagnosis cannot have eligible hypotheses"
        )
    if document["analysis"] is not None:
        analysis = _object(
            document["analysis"],
            {
                "compatibility_key_sha256", "independent_experiment_count",
                "complete_blocks_per_experiment", "experiments", "combined",
                "strata", "association_claim",
            },
            "diagnosis.analysis",
        )
        _digest(
            analysis["compatibility_key_sha256"],
            "diagnosis.analysis.compatibility_key_sha256",
        )
        _integer(
            analysis["independent_experiment_count"],
            "diagnosis.analysis.independent_experiment_count",
        )
        block_rows = require_array(
            analysis["complete_blocks_per_experiment"],
            "diagnosis.analysis.complete_blocks_per_experiment",
        )
        for index, raw_row in enumerate(block_rows):
            path = f"diagnosis.analysis.complete_blocks_per_experiment[{index}]"
            row = _object(
                raw_row,
                {"experiment_id", "campaign_id", "complete_block_count"},
                path,
            )
            require_identifier(row["experiment_id"], f"{path}.experiment_id")
            require_identifier(row["campaign_id"], f"{path}.campaign_id")
            _integer(
                row["complete_block_count"],
                f"{path}.complete_block_count",
            )
        experiment_rows = require_array(
            analysis["experiments"], "diagnosis.analysis.experiments"
        )
        for index, raw_row in enumerate(experiment_rows):
            path = f"diagnosis.analysis.experiments[{index}]"
            row = _object(
                raw_row,
                {"experiment_id", "campaign_id", "point_estimate"},
                path,
            )
            require_identifier(row["experiment_id"], f"{path}.experiment_id")
            require_identifier(row["campaign_id"], f"{path}.campaign_id")
            _number(row["point_estimate"], f"{path}.point_estimate")
        strata = require_array(
            analysis["strata"], "diagnosis.analysis.strata", nonempty=True
        )
        stratum_keys: list[str] = []
        for index, raw_stratum in enumerate(strata):
            path = f"diagnosis.analysis.strata[{index}]"
            stratum = _object(
                raw_stratum,
                {
                    "compatibility_key_sha256",
                    "status",
                    "experiment_bindings",
                    "evidence_entry_ids",
                    "evidence_sha256",
                },
                path,
            )
            stratum_keys.append(
                _digest(
                    stratum["compatibility_key_sha256"],
                    f"{path}.compatibility_key_sha256",
                )
            )
            require_enum(
                stratum["status"],
                {"eligible", "no_miss", "insufficient", "contradictory"},
                f"{path}.status",
            )
            bindings = require_array(
                stratum["experiment_bindings"],
                f"{path}.experiment_bindings",
            )
            binding_identities: list[tuple[str, str]] = []
            for binding_index, raw_binding in enumerate(bindings):
                binding_path = (
                    f"{path}.experiment_bindings[{binding_index}]"
                )
                binding = _object(
                    raw_binding,
                    {"experiment_id", "campaign_id"},
                    binding_path,
                )
                binding_identities.append(
                    (
                        require_identifier(
                            binding["experiment_id"],
                            f"{binding_path}.experiment_id",
                        ),
                        require_identifier(
                            binding["campaign_id"],
                            f"{binding_path}.campaign_id",
                        ),
                    )
                )
            if binding_identities != sorted(binding_identities):
                raise ContractError(
                    f"{path}.experiment_bindings must be canonically sorted"
                )
            evidence_ids = require_string_array(
                stratum["evidence_entry_ids"],
                f"{path}.evidence_entry_ids",
            )
            for evidence_index, entry_id in enumerate(evidence_ids):
                require_identifier(
                    entry_id,
                    f"{path}.evidence_entry_ids[{evidence_index}]",
                )
            if evidence_ids != sorted(evidence_ids):
                raise ContractError(
                    f"{path}.evidence_entry_ids must be canonically sorted"
                )
            _hashes(stratum["evidence_sha256"], f"{path}.evidence_sha256")
        if stratum_keys != sorted(stratum_keys):
            raise ContractError(
                "diagnosis.analysis.strata must be canonically sorted"
            )
        combined = _object(
            analysis["combined"],
            {
                "point_estimate", "bootstrap_mean", "interval_lower",
                "interval_upper", "interval_level",
                "minimum_practical_effect", "monte_carlo_standard_error",
            },
            "diagnosis.analysis.combined",
        )
        for field in (
            "point_estimate", "bootstrap_mean", "interval_lower",
            "interval_upper", "interval_level", "minimum_practical_effect",
        ):
            _number(combined[field], f"diagnosis.analysis.combined.{field}")
        mcse = _object(
            combined["monte_carlo_standard_error"],
            {"bootstrap_mean", "interval_lower", "interval_upper"},
            "diagnosis.analysis.combined.monte_carlo_standard_error",
        )
        for field in mcse:
            _number(
                mcse[field],
                "diagnosis.analysis.combined."
                f"monte_carlo_standard_error.{field}",
                minimum=0,
            )
        require_enum(
            analysis["association_claim"],
            {"synthetic_creative_feature_associated_with_registered_outcome"},
            "diagnosis.analysis.association_claim",
        )
    causes = _object(document["alternative_causes"], {"delivery", "targeting", "timing", "offer", "landing_page", "tracking", "attribution"}, "diagnosis.alternative_causes")
    for cause, raw_cause in causes.items():
        checked_cause = _object(
            raw_cause,
            {"status", "evidence_sha256", "rationale"},
            f"diagnosis.alternative_causes.{cause}",
        )
        require_enum(
            checked_cause["status"],
            {"cleared", "not_cleared", "unknown"},
            f"diagnosis.alternative_causes.{cause}.status",
        )
        _digest(
            checked_cause["evidence_sha256"],
            f"diagnosis.alternative_causes.{cause}.evidence_sha256",
        )
        require_string(
            checked_cause["rationale"],
            f"diagnosis.alternative_causes.{cause}.rationale",
        )
    require_string_array(document["limitations"], "diagnosis.limitations")
    return _require_self_hash(document, "diagnosis_sha256", "diagnosis")


def _proposal_operation(value: object, path: str) -> dict[str, object]:
    keys = {
        "operation_type", "target_persona_id", "hypothesis_id",
        "proposed_after", "changed_fields", "evidence_sha256",
        "creative_attribute_registry_sha256", "rationale", "constraints",
        "reversibility",
    }
    document = _object(value, keys, path)
    require_enum(
        document["operation_type"],
        {"profile_snapshot_update"},
        f"{path}.operation_type",
    )
    require_identifier(document["target_persona_id"], f"{path}.target_persona_id")
    require_identifier(document["hypothesis_id"], f"{path}.hypothesis_id")
    changed = require_string_array(
        document["changed_fields"], f"{path}.changed_fields", nonempty=True
    )
    if len(changed) != 1 or changed[0] not in ALLOWED_PERSONA_FIELDS:
        raise ContractError(
            f"{path}.changed_fields must contain exactly one allowed "
            "persona behavior field"
        )
    proposed = _object(
        document["proposed_after"], set(changed), f"{path}.proposed_after"
    )
    _persona_behavior_value(
        proposed[changed[0]],
        field=changed[0],
        path=f"{path}.proposed_after.{changed[0]}",
    )
    _hashes(
        document["evidence_sha256"],
        f"{path}.evidence_sha256",
        nonempty=True,
    )
    _digest(
        document["creative_attribute_registry_sha256"],
        f"{path}.creative_attribute_registry_sha256",
    )
    require_string(document["rationale"], f"{path}.rationale")
    require_string_array(document["constraints"], f"{path}.constraints")
    require_enum(
        document["reversibility"],
        {"sandbox_reversible"},
        f"{path}.reversibility",
    )
    return document


def _operation(value: object, path: str) -> dict[str, object]:
    keys = {"operation_type", "target_persona_id", "target_persona_snapshot_sha256", "hypothesis_id", "before", "proposed_after", "changed_fields", "evidence_sha256", "creative_attribute_registry_sha256", "rationale", "constraints", "reversibility"}
    document = _object(value, keys, path)
    require_enum(document["operation_type"], {"profile_snapshot_update"}, f"{path}.operation_type")
    require_identifier(document["target_persona_id"], f"{path}.target_persona_id")
    _digest(document["target_persona_snapshot_sha256"], f"{path}.target_persona_snapshot_sha256")
    require_identifier(document["hypothesis_id"], f"{path}.hypothesis_id")
    changed = require_string_array(document["changed_fields"], f"{path}.changed_fields", nonempty=True)
    if not set(changed) <= ALLOWED_PERSONA_FIELDS:
        raise ContractError(f"{path}.changed_fields must be allowed persona behavior fields")
    if len(changed) != len(set(changed)):
        raise ContractError(f"{path}.changed_fields must contain unique values")
    if changed != sorted(changed):
        raise ContractError(f"{path}.changed_fields must be canonically sorted")
    for key in {"before", "proposed_after"}:
        member = _object(document[key], set(changed), f"{path}.{key}")
        for field in changed:
            _persona_behavior_value(
                member[field],
                field=field,
                path=f"{path}.{key}.{field}",
            )
    _hashes(document["evidence_sha256"], f"{path}.evidence_sha256", nonempty=True)
    _digest(document["creative_attribute_registry_sha256"], f"{path}.creative_attribute_registry_sha256")
    require_string(document["rationale"], f"{path}.rationale")
    require_string_array(document["constraints"], f"{path}.constraints")
    require_enum(document["reversibility"], {"sandbox_reversible"}, f"{path}.reversibility")
    return document


def validate_experimental_proposal(payload: object) -> dict[str, object]:
    keys = {
        "schema_version", "proposal_id", "proposed_at", "status", "evidence_origin",
        "real_world_validation_status", "production_executable", "sandbox_candidate_materialization_permitted",
        "production_candidate_materialization_permitted", "activation_permitted", "active_panel_mutation_permitted",
        "base_panel_binding", "base_panel_authority_status",
        "synthetic_study_binding",
        "evidence_library_projection_binding", "evidence_head_receipt_binding",
        "frozen_analysis_bindings", "diagnosis", "proposal_type", "operation",
        "expected_effect", "alternative_explanations",
        "assumptions", "uncertainty", "known_risks", "required_review", "reversibility", "limitations", "proposal_sha256",
    }
    document = _object(payload, keys, "proposal")
    require_enum(document["schema_version"], {PROPOSAL_VERSION}, "proposal.schema_version")
    require_identifier(document["proposal_id"], "proposal.proposal_id")
    require_timestamp(document["proposed_at"], "proposal.proposed_at")
    _exact_constants(document, _EXPERIMENTAL_STATES, "proposal")
    _binding(document["base_panel_binding"], "proposal.base_panel_binding")
    require_enum(
        document["base_panel_authority_status"],
        {"unverified_proposal_context"},
        "proposal.base_panel_authority_status",
    )
    _study_binding(document["synthetic_study_binding"], "proposal.synthetic_study_binding")
    projection = _object(
        document["evidence_library_projection_binding"],
        {"library_id", "as_of", "library_sha256", "projection_sha256"},
        "proposal.evidence_library_projection_binding",
    )
    require_identifier(
        projection["library_id"],
        "proposal.evidence_library_projection_binding.library_id",
    )
    require_timestamp(
        projection["as_of"],
        "proposal.evidence_library_projection_binding.as_of",
    )
    for field in ("library_sha256", "projection_sha256"):
        _digest(
            projection[field],
            f"proposal.evidence_library_projection_binding.{field}",
        )
    receipt = _object(
        document["evidence_head_receipt_binding"],
        {
            "receipt_id", "receipt_sha256", "event_count",
            "event_sha256", "projection_sha256",
        },
        "proposal.evidence_head_receipt_binding",
    )
    require_identifier(
        receipt["receipt_id"],
        "proposal.evidence_head_receipt_binding.receipt_id",
    )
    _integer(
        receipt["event_count"],
        "proposal.evidence_head_receipt_binding.event_count",
        minimum=1,
    )
    for field in ("receipt_sha256", "event_sha256", "projection_sha256"):
        _digest(
            receipt[field],
            f"proposal.evidence_head_receipt_binding.{field}",
        )
    if receipt["projection_sha256"] != projection["projection_sha256"]:
        raise ContractError(
            "proposal evidence projection and receipt hashes must match"
        )
    frozen = _object(
        document["frozen_analysis_bindings"],
        {
            "diagnosis_method_version", "diagnosis_method_sha256",
            "monte_carlo_error_method_version",
            "monte_carlo_error_method_sha256", "estimand_sha256",
            "stopping_rule_sha256", "experiment_design_sha256",
            "creative_attribute_registry_sha256",
        },
        "proposal.frozen_analysis_bindings",
    )
    require_enum(
        frozen["diagnosis_method_version"],
        {DIAGNOSIS_METHOD_VERSION},
        "proposal.frozen_analysis_bindings.diagnosis_method_version",
    )
    require_enum(
        frozen["monte_carlo_error_method_version"],
        {"deterministic-batch-quantile-mcse-v1"},
        "proposal.frozen_analysis_bindings.monte_carlo_error_method_version",
    )
    for field in (
        "diagnosis_method_sha256", "monte_carlo_error_method_sha256",
        "estimand_sha256", "stopping_rule_sha256",
        "creative_attribute_registry_sha256",
    ):
        _digest(frozen[field], f"proposal.frozen_analysis_bindings.{field}")
    _hashes(
        frozen["experiment_design_sha256"],
        "proposal.frozen_analysis_bindings.experiment_design_sha256",
        nonempty=True,
    )
    diagnosis = _object(
        document["diagnosis"],
        {"diagnosis_id", "diagnosis_sha256", "decision"},
        "proposal.diagnosis",
    )
    require_identifier(diagnosis["diagnosis_id"], "proposal.diagnosis.diagnosis_id")
    _digest(diagnosis["diagnosis_sha256"], "proposal.diagnosis.diagnosis_sha256")
    require_enum(
        diagnosis["decision"],
        {"repeatable_behavioral_miss", "no_repeatable_miss"},
        "proposal.diagnosis.decision",
    )
    effect = _object(
        document["expected_effect"],
        {"direction", "claim_boundary"},
        "proposal.expected_effect",
    )
    require_enum(effect["direction"], {"positive", "negative", "none"}, "proposal.expected_effect.direction")
    require_enum(
        effect["claim_boundary"],
        {"synthetic_hypothesis_to_test", "no_change_supported_in_fixture"},
        "proposal.expected_effect.claim_boundary",
    )
    causes = _object(document["alternative_explanations"], {"delivery", "targeting", "timing", "offer", "landing_page", "tracking", "attribution"}, "proposal.alternative_explanations")
    for cause, raw_cause in causes.items():
        checked = _object(
            raw_cause,
            {"status", "evidence_sha256", "rationale"},
            f"proposal.alternative_explanations.{cause}",
        )
        require_enum(
            checked["status"],
            {"cleared", "not_cleared", "unknown"},
            f"proposal.alternative_explanations.{cause}.status",
        )
        _digest(
            checked["evidence_sha256"],
            f"proposal.alternative_explanations.{cause}.evidence_sha256",
        )
        require_string(
            checked["rationale"],
            f"proposal.alternative_explanations.{cause}.rationale",
        )
    assumptions = _object(
        document["assumptions"],
        {
            "synthetic_fixture_only",
            "panel_validation_deferred_to_candidate_materialization",
        },
        "proposal.assumptions",
    )
    if any(type(value) is not bool or value is not True for value in assumptions.values()):
        raise ContractError("proposal assumptions must be exactly true")
    uncertainty = _object(
        document["uncertainty"],
        {"status", "monte_carlo_standard_error"},
        "proposal.uncertainty",
    )
    require_enum(
        uncertainty["status"],
        {"experimental"},
        "proposal.uncertainty.status",
    )
    if uncertainty["monte_carlo_standard_error"] is not None:
        mcse = _object(
            uncertainty["monte_carlo_standard_error"],
            {"bootstrap_mean", "interval_lower", "interval_upper"},
            "proposal.uncertainty.monte_carlo_standard_error",
        )
        for field in mcse:
            _number(
                mcse[field],
                f"proposal.uncertainty.monte_carlo_standard_error.{field}",
                minimum=0,
            )
    review = _object(
        document["required_review"],
        {"status", "real_world_evidence_required_for_activation"},
        "proposal.required_review",
    )
    require_enum(
        review["status"], {"required"}, "proposal.required_review.status"
    )
    if (
        type(review["real_world_evidence_required_for_activation"]) is not bool
        or review["real_world_evidence_required_for_activation"] is not True
    ):
        raise ContractError(
            "proposal requires real-world evidence before activation"
        )
    for key in {"known_risks", "limitations"}: require_string_array(document[key], f"proposal.{key}")
    reversibility = _object(
        document["reversibility"],
        {"status"},
        "proposal.reversibility",
    )
    require_enum(
        reversibility["status"],
        {"sandbox_candidate_can_be_discarded"},
        "proposal.reversibility.status",
    )
    proposal_type = require_enum(document["proposal_type"], {"no_change", "profile_snapshot_update"}, "proposal.proposal_type")
    if proposal_type == "no_change":
        if document["operation"] is not None:
            raise ContractError("proposal.operation must be null for no_change")
        if diagnosis["decision"] != "no_repeatable_miss":
            raise ContractError(
                "no_change proposal requires no_repeatable_miss diagnosis"
            )
    else:
        operation = _proposal_operation(document["operation"], "proposal.operation")
        if diagnosis["decision"] != "repeatable_behavioral_miss":
            raise ContractError(
                "update proposal requires repeatable_behavioral_miss diagnosis"
            )
        if operation["target_persona_id"] != document["base_panel_binding"]["persona_id"]:
            raise ContractError("proposal.operation.target_persona_id must match base persona")
    return _require_self_hash(document, "proposal_sha256", "proposal")


def validate_persona_authoring_projection(payload: object) -> dict[str, object]:
    keys = {"schema_version", "projection_id", "created_at", "source_role", "provenance_status", "panel_binding", "persona_archetypes", "grounded_profile_snapshot_bindings", "projection_sha256"}
    document = _object(payload, keys, "authoring_projection")
    require_enum(document["schema_version"], {AUTHORING_PROJECTION_VERSION}, "authoring_projection.schema_version")
    require_identifier(document["projection_id"], "authoring_projection.projection_id")
    require_timestamp(document["created_at"], "authoring_projection.created_at")
    require_enum(document["source_role"], {"saved-audience-panel-v3.persona_archetypes"}, "authoring_projection.source_role")
    require_enum(document["provenance_status"], {"canonical_panel_projection_only"}, "authoring_projection.provenance_status")
    panel_binding = _binding(
        document["panel_binding"], "authoring_projection.panel_binding"
    )
    personas = require_array(document["persona_archetypes"], "authoring_projection.persona_archetypes", nonempty=True)
    ids: list[str] = []
    for index, persona in enumerate(personas):
        member = _object(persona, {"persona_archetype_id", "anxieties", "decision_context", "motivations", "proof_needs", "role_context"}, f"authoring_projection.persona_archetypes[{index}]")
        ids.append(require_identifier(member["persona_archetype_id"], f"authoring_projection.persona_archetypes[{index}].persona_archetype_id"))
        for field in ALLOWED_PERSONA_FIELDS:
            value = member[field]
            _persona_behavior_value(
                value,
                field=field,
                path=f"authoring_projection.persona_archetypes[{index}].{field}",
            )
    if len(ids) != len(set(ids)): raise ContractError("authoring_projection.persona_archetypes must have unique persona identifiers")
    if panel_binding["persona_id"] not in ids:
        raise ContractError(
            "authoring_projection.panel_binding.persona_id must reference an "
            "included persona archetype"
        )
    bound_persona = next(
        persona
        for persona in personas
        if persona["persona_archetype_id"] == panel_binding["persona_id"]
    )
    bound_snapshot = {
        field: bound_persona[field] for field in ALLOWED_PERSONA_FIELDS
    }
    if panel_binding["persona_snapshot_sha256"] != sha256_json(bound_snapshot):
        raise ContractError(
            "authoring_projection.panel_binding.persona_snapshot_sha256 "
            "must match the projected persona"
        )
    bindings = require_array(document["grounded_profile_snapshot_bindings"], "authoring_projection.grounded_profile_snapshot_bindings")
    profile_ids: list[str] = []
    for index, binding in enumerate(bindings):
        member = _object(binding, {"profile_id", "persona_archetype_id", "profile_snapshot", "profile_snapshot_sha256"}, f"authoring_projection.grounded_profile_snapshot_bindings[{index}]")
        profile_ids.append(require_identifier(member["profile_id"], f"authoring_projection.grounded_profile_snapshot_bindings[{index}].profile_id"))
        persona_id = require_identifier(member["persona_archetype_id"], f"authoring_projection.grounded_profile_snapshot_bindings[{index}].persona_archetype_id")
        if persona_id not in ids:
            raise ContractError(
                "authoring_projection grounded profile must reference an "
                "included persona archetype"
            )
        snapshot = _object(member["profile_snapshot"], set(ALLOWED_PERSONA_FIELDS), f"authoring_projection.grounded_profile_snapshot_bindings[{index}].profile_snapshot")
        for field in ALLOWED_PERSONA_FIELDS:
            value = snapshot[field]
            _persona_behavior_value(
                value,
                field=field,
                path=(
                    "authoring_projection.grounded_profile_snapshot_bindings"
                    f"[{index}].profile_snapshot.{field}"
                ),
            )
        if _digest(member["profile_snapshot_sha256"], f"authoring_projection.grounded_profile_snapshot_bindings[{index}].profile_snapshot_sha256") != sha256_json(snapshot):
            raise ContractError(f"authoring_projection.grounded_profile_snapshot_bindings[{index}].profile_snapshot_sha256 does not match canonical content")
    if len(profile_ids) != len(set(profile_ids)):
        raise ContractError(
            "authoring_projection.grounded_profile_snapshot_bindings must "
            "have unique profile identifiers"
        )
    return _require_self_hash(document, "projection_sha256", "authoring_projection")


def validate_sandbox_candidate_binding(payload: object) -> dict[str, object]:
    keys = {"schema_version", "candidate_id", "created_at", "status", "evidence_origin", "real_world_validation_status", "registration_permitted", "activation_permitted", "active_panel_mutation_permitted", "base_panel_binding", "proposal_binding", "candidate_panel_binding", "base_authoring_projection_binding", "candidate_authoring_projection_binding", "applied_operation", "allowed_diff", "forbidden_diff_check", "structural_validation", "synthetic_evaluation_requirement", "limitations", "candidate_binding_sha256"}
    document = _object(payload, keys, "candidate_binding")
    require_enum(document["schema_version"], {CANDIDATE_VERSION}, "candidate_binding.schema_version")
    require_identifier(document["candidate_id"], "candidate_binding.candidate_id")
    require_timestamp(document["created_at"], "candidate_binding.created_at")
    _exact_constants(document, _CANDIDATE_STATES, "candidate_binding")
    base = _object(
        document["base_panel_binding"],
        {
            "panel_id", "panel_version", "panel_sha256", "persona_id",
            "persona_snapshot_sha256",
        },
        "candidate_binding.base_panel_binding",
    )
    _binding(
        {
            key: base[key]
            for key in (
                "panel_id", "panel_version", "panel_sha256", "persona_id",
                "persona_snapshot_sha256",
            )
        },
        "candidate_binding.base_panel_binding",
    )
    proposal = _object(document["proposal_binding"], {"proposal_id", "proposal_sha256"}, "candidate_binding.proposal_binding")
    require_identifier(proposal["proposal_id"], "candidate_binding.proposal_binding.proposal_id")
    _digest(proposal["proposal_sha256"], "candidate_binding.proposal_binding.proposal_sha256")
    candidate_panel = _binding(
        document["candidate_panel_binding"],
        "candidate_binding.candidate_panel_binding",
    )
    base_version = _semantic_version(
        base["panel_version"],
        "candidate_binding.base_panel_binding.panel_version",
    )
    candidate_version = _semantic_version(
        candidate_panel["panel_version"],
        "candidate_binding.candidate_panel_binding.panel_version",
    )
    if tuple(map(int, candidate_version.split("."))) <= tuple(
        map(int, base_version.split("."))
    ):
        raise ContractError(
            "candidate_binding candidate panel version must be strictly newer"
        )
    if candidate_panel["panel_id"] != base["panel_id"]:
        raise ContractError(
            "candidate_binding base and candidate panel IDs must match"
        )
    if candidate_panel["persona_id"] != base["persona_id"]:
        raise ContractError(
            "candidate_binding base and candidate persona IDs must match"
        )
    for key in {"base_authoring_projection_binding", "candidate_authoring_projection_binding"}:
        projection = _object(document[key], {"projection_id", "projection_sha256"}, f"candidate_binding.{key}")
        require_identifier(projection["projection_id"], f"candidate_binding.{key}.projection_id")
        _digest(projection["projection_sha256"], f"candidate_binding.{key}.projection_sha256")
    operation = _operation(document["applied_operation"], "candidate_binding.applied_operation")
    if len(operation["changed_fields"]) != 1:
        raise ContractError(
            "candidate_binding.applied_operation must change exactly one field"
        )
    if operation["target_persona_id"] != base["persona_id"]:
        raise ContractError(
            "candidate_binding applied operation must target the bound persona"
        )
    if (
        operation["target_persona_snapshot_sha256"]
        != base["persona_snapshot_sha256"]
    ):
        raise ContractError(
            "candidate_binding applied operation must bind the base persona "
            "snapshot"
        )
    allowed = _object(document["allowed_diff"], {"changed_paths"}, "candidate_binding.allowed_diff")
    paths = require_string_array(allowed["changed_paths"], "candidate_binding.allowed_diff.changed_paths", nonempty=True)
    if paths != sorted(paths): raise ContractError("candidate_binding.allowed_diff.changed_paths must be canonically sorted")
    expected_paths = {"$.version", "$.created_at", "$.updated_at"}
    field = operation["changed_fields"][0]
    persona_id = operation["target_persona_id"]
    expected_paths.add(f"$.persona_archetypes[{persona_id}].{field}")
    grounded_pattern = re.compile(
        r"^\$\.grounded_context_profiles\["
        r"([a-z][a-z0-9]*(?:-[a-z0-9]+)*)"
        rf"\]\.profile_snapshot\.{re.escape(field)}$"
    )
    grounded_paths = [path for path in paths if grounded_pattern.fullmatch(path)]
    if not grounded_paths:
        raise ContractError(
            "candidate_binding.allowed_diff.changed_paths must include at "
            "least one matching grounded-profile snapshot"
        )
    if set(paths) != expected_paths | set(grounded_paths):
        raise ContractError("candidate_binding.allowed_diff.changed_paths must be derived from the applied operation")
    forbidden = _object(document["forbidden_diff_check"], {"passed", "forbidden_paths"}, "candidate_binding.forbidden_diff_check")
    if type(forbidden["passed"]) is not bool or forbidden["passed"] is not True: raise ContractError("candidate_binding.forbidden_diff_check.passed must be true")
    require_string_array(forbidden["forbidden_paths"], "candidate_binding.forbidden_diff_check.forbidden_paths")
    structural = _object(
        document["structural_validation"],
        {
            "standalone_saved_panel_v3",
            "production_workflow_state",
            "production_construction_audit",
            "production_package_approval",
            "production_library_registration",
        },
        "candidate_binding.structural_validation",
    )
    require_enum(
        structural["standalone_saved_panel_v3"],
        {"passed"},
        "candidate_binding.structural_validation.standalone_saved_panel_v3",
    )
    for field in (
        "production_workflow_state",
        "production_construction_audit",
        "production_package_approval",
        "production_library_registration",
    ):
        require_enum(
            structural[field],
            {"not_run_sandbox_only"},
            f"candidate_binding.structural_validation.{field}",
        )
    requirement = _object(document["synthetic_evaluation_requirement"], {"required"}, "candidate_binding.synthetic_evaluation_requirement")
    if type(requirement["required"]) is not bool or requirement["required"] is not True: raise ContractError("candidate_binding.synthetic_evaluation_requirement.required must be true")
    require_string_array(document["limitations"], "candidate_binding.limitations")
    return _require_self_hash(document, "candidate_binding_sha256", "candidate_binding")


def _expected_exercise_panel_ref(
    *,
    panel_id: str,
    panel_version: str,
    panel_kind: str,
    candidate_id: str | None,
) -> str:
    preimage = {
        "candidate_id": candidate_id,
        "panel_id": panel_id,
        "panel_kind": panel_kind,
        "panel_version": panel_version,
    }
    return (
        "exercise-panel-"
        + sha256_json(preimage).removeprefix("sha256:")[:24]
    )


def _exercise_assignment_projection(
    jobs: object,
) -> dict[str, object]:
    """Derive the closed execution assignment from ordinary job bytes."""

    values = require_array(jobs, "exercise assignment jobs", nonempty=True)
    phase_by_shape = {
        ("screening_response", "complete_exposure"):
            "complete-exposure",
        ("screening_response", "partial_exposure_maxdiff"):
            "maxdiff-screening",
        ("boundary_response", "partial_exposure_maxdiff"):
            "pairwise-boundary",
        ("finalist_response", "complete_exposure"):
            "finalist-verbatim",
    }
    bindings: list[dict[str, object]] = []
    unique_id_fields = {
        "dispatch_id": set(),
        "response_id": set(),
        "synthetic_replicate_id": set(),
        "audience_slot_id": set(),
    }
    for index, raw in enumerate(values):
        if not isinstance(raw, Mapping):
            raise ContractError(
                f"exercise assignment jobs[{index}] must be an object"
            )
        job = dict(raw)
        shape = (job.get("record_type"), job.get("method"))
        phase = phase_by_shape.get(shape)
        if phase is None:
            raise ContractError(
                "exercise assignment contains an unknown Ad Testing path"
            )
        dispatch_id = require_identifier(
            job.get("dispatch_id"),
            f"exercise assignment jobs[{index}].dispatch_id",
        )
        response_id = require_identifier(
            job.get("response_id"),
            f"exercise assignment jobs[{index}].response_id",
        )
        replicate_id = require_identifier(
            job.get("synthetic_replicate_id"),
            (
                "exercise assignment jobs"
                f"[{index}].synthetic_replicate_id"
            ),
        )
        audience_slot_id = require_identifier(
            job.get("audience_slot_id"),
            f"exercise assignment jobs[{index}].audience_slot_id",
        )
        for field, value in (
            ("dispatch_id", dispatch_id),
            ("response_id", response_id),
            ("synthetic_replicate_id", replicate_id),
            ("audience_slot_id", audience_slot_id),
        ):
            if value in unique_id_fields[field]:
                raise ContractError(
                    f"exercise assignment {field} values must be unique"
                )
            unique_id_fields[field].add(value)
        binding = {
            "phase": phase,
            "dispatch_id": dispatch_id,
            "response_id": response_id,
            "synthetic_replicate_id": replicate_id,
            "audience_slot_id": audience_slot_id,
            "segment_id": require_identifier(
                job.get("segment_id"),
                f"exercise assignment jobs[{index}].segment_id",
            ),
            "context_stratum_id": require_identifier(
                job.get("context_stratum_id"),
                f"exercise assignment jobs[{index}].context_stratum_id",
            ),
            "grounded_profile_id": require_identifier(
                job.get("grounded_profile_id"),
                f"exercise assignment jobs[{index}].grounded_profile_id",
            ),
            "variation_ids": require_string_array(
                job.get("variation_ids"),
                f"exercise assignment jobs[{index}].variation_ids",
                nonempty=True,
            ),
            "shown_order": require_string_array(
                job.get("shown_order"),
                f"exercise assignment jobs[{index}].shown_order",
                nonempty=True,
            ),
            "job_sha256": sha256_json(job),
        }
        bindings.append(binding)
    bindings.sort(key=lambda row: str(row["dispatch_id"]))
    return {
        "schema_version":
            "synthetic-exercise-assignment-projection-v1",
        "job_bindings": bindings,
    }


def validate_candidate_seal_envelope(
    payload: object,
) -> dict[str, object]:
    """Authenticate one exact Task 6 bundle and persistent phase receipt."""

    envelope = _object(
        payload,
        {
            "materialized_candidate",
            "sealed_bundle_manifest",
            "candidate_seal_receipt",
        },
        "candidate_seal_envelope",
    )
    from .candidate import _README, _authenticate_materialized

    materialized = envelope["materialized_candidate"]
    if not isinstance(materialized, dict):
        raise ContractError("Task 6 materialized candidate must be an object")
    binding, candidate_panel, _, _ = _authenticate_materialized(
        deepcopy(materialized)
    )
    payloads = {
        "experimental-candidate-binding.json": canonical_json_bytes(
            materialized["candidate_binding"]
        ),
        "base-persona-authoring-projection.json": canonical_json_bytes(
            materialized["base_authoring_projection"]
        ),
        "candidate-persona-authoring-projection.json": canonical_json_bytes(
            materialized["candidate_authoring_projection"]
        ),
        "base-persona-snapshot.json": canonical_json_bytes(
            materialized["base_persona_snapshot"]
        ),
        "candidate-persona-snapshot.json": canonical_json_bytes(
            materialized["candidate_persona_snapshot"]
        ),
        "candidate-audience-panel.json": canonical_json_bytes(candidate_panel),
        "persona-behavior-diff.json": canonical_json_bytes(
            materialized["persona_behavior_diff"]
        ),
        "experimental-proposal.json": canonical_json_bytes(
            materialized["experimental_proposal"]
        ),
        "standalone-panel-validation.json": canonical_json_bytes(
            materialized["standalone_panel_validation"]
        ),
        "README.txt": _README.encode("utf-8"),
    }
    expected_manifest = {
        "schema_version":
            "experimental-persona-candidate-bundle-manifest-v1",
        "candidate_id": binding["candidate_id"],
        "registration_permitted": False,
        "production_package_manifest_present": False,
        "production_package_graph_present": False,
        "files": [
            {
                "path": name,
                "sha256": (
                    "sha256:" + hashlib.sha256(raw).hexdigest()
                ),
                "byte_count": len(raw),
            }
            for name, raw in sorted(payloads.items())
        ],
        "bundle_manifest_sha256": None,
    }
    expected_manifest["bundle_manifest_sha256"] = sha256_json(
        expected_manifest
    )
    if canonical_json_bytes(envelope["sealed_bundle_manifest"]) != (
        canonical_json_bytes(expected_manifest)
    ):
        raise ContractError(
            "candidate does not byte-match its Task 6 sealed bundle manifest"
        )
    payloads["bundle-manifest.json"] = canonical_json_bytes(
        expected_manifest
    )
    directory_receipt = {
        "schema_version":
            "experimental-calibration-directory-output-receipt-v1",
        "candidate_id": binding["candidate_id"],
        "files": [
            {
                "path": name,
                "byte_count": len(payloads[name]),
                "raw_bytes_sha256": (
                    "sha256:" + hashlib.sha256(payloads[name]).hexdigest()
                ),
            }
            for name in sorted(payloads)
        ],
        "tree_sha256": None,
    }
    directory_receipt["tree_sha256"] = sha256_json(directory_receipt)
    receipt = _object(
        envelope["candidate_seal_receipt"],
        {
            "schema_version",
            "engine_entrypoint",
            "arguments_sha256",
            "admitted_input_tree_sha256",
            "input_manifest_sha256",
            "first_party_source_closure_sha256",
            "external_dependency_closure_sha256",
            "source_manifest_sha256",
            "runtime_binding_sha256",
            "output",
            "phase_execution_receipt_sha256",
        },
        "candidate_seal_receipt",
    )
    require_enum(
        receipt["schema_version"],
        {"experimental-calibration-phase-execution-receipt-v1"},
        "candidate_seal_receipt.schema_version",
    )
    require_enum(
        receipt["engine_entrypoint"],
        {"materialize"},
        "candidate_seal_receipt.engine_entrypoint",
    )
    for field in (
        "arguments_sha256",
        "admitted_input_tree_sha256",
        "input_manifest_sha256",
        "first_party_source_closure_sha256",
        "external_dependency_closure_sha256",
        "source_manifest_sha256",
        "runtime_binding_sha256",
    ):
        _digest(receipt[field], f"candidate_seal_receipt.{field}")
    output = _object(
        receipt["output"],
        {"kind", "name", "output_sha256"},
        "candidate_seal_receipt.output",
    )
    if output != {
        "kind": "directory",
        "name": "result",
        "output_sha256": directory_receipt["tree_sha256"],
    }:
        raise ContractError(
            "candidate seal receipt does not bind the sealed bundle bytes"
        )
    supplied = _digest(
        receipt["phase_execution_receipt_sha256"],
        "candidate_seal_receipt.phase_execution_receipt_sha256",
    )
    unhashed = deepcopy(receipt)
    unhashed["phase_execution_receipt_sha256"] = None
    if supplied != sha256_json(unhashed):
        raise ContractError("candidate seal receipt hash is stale")
    return deepcopy(envelope)


def validate_synthetic_exercise(payload: object) -> dict[str, object]:
    keys = {
        "schema_version",
        "exercise_id",
        "exercised_at",
        "study_manifest_binding",
        "creative_attribute_registry_binding",
        "frozen_adapter_binding",
        "public_scenario_bindings",
        "panel_bindings",
        "panel_rosters",
        "panelist_jobs",
        "run_results",
        "production_authority",
        "limitations",
        "exercise_sha256",
    }
    document = _object(payload, keys, "exercise")
    require_enum(
        document["schema_version"],
        {EXERCISE_VERSION},
        "exercise.schema_version",
    )
    require_identifier(document["exercise_id"], "exercise.exercise_id")
    require_timestamp(document["exercised_at"], "exercise.exercised_at")
    study_binding = _study_binding(
        document["study_manifest_binding"],
        "exercise.study_manifest_binding",
    )
    registry_binding = _object(
        document["creative_attribute_registry_binding"],
        {"registry_id", "registry_sha256"},
        "exercise.creative_attribute_registry_binding",
    )
    require_identifier(
        registry_binding["registry_id"],
        "exercise.creative_attribute_registry_binding.registry_id",
    )
    _digest(
        registry_binding["registry_sha256"],
        "exercise.creative_attribute_registry_binding.registry_sha256",
    )
    adapter = _object(
        document["frozen_adapter_binding"],
        {
            "adapter_id",
            "adapter_version",
            "source_sha256",
            "first_read_sha256",
            "second_read_sha256",
            "ast_sha256",
            "feature_allowlist",
            "deterministic_tie_rule",
        },
        "exercise.frozen_adapter_binding",
    )
    require_identifier(
        adapter["adapter_id"], "exercise.frozen_adapter_binding.adapter_id"
    )
    _semantic_version(
        adapter["adapter_version"],
        "exercise.frozen_adapter_binding.adapter_version",
    )
    for field in (
        "source_sha256",
        "first_read_sha256",
        "second_read_sha256",
        "ast_sha256",
    ):
        _digest(adapter[field], f"exercise.frozen_adapter_binding.{field}")
    if not (
        adapter["source_sha256"]
        == adapter["first_read_sha256"]
        == adapter["second_read_sha256"]
    ):
        raise ContractError("exercise adapter byte reads must match exactly")
    require_string_array(
        adapter["feature_allowlist"],
        "exercise.frozen_adapter_binding.feature_allowlist",
        nonempty=True,
    )
    require_enum(
        adapter["deterministic_tie_rule"],
        _SYNTHETIC_RESPONSE_TIE_RULES,
        "exercise.frozen_adapter_binding.deterministic_tie_rule",
    )

    scenarios = require_array(
        document["public_scenario_bindings"],
        "exercise.public_scenario_bindings",
        nonempty=True,
    )
    scenario_repetitions: dict[str, int] = {}
    scenario_digests: dict[str, tuple[str, str, str]] = {}
    for index, raw in enumerate(scenarios):
        path = f"exercise.public_scenario_bindings[{index}]"
        row = _object(
            raw,
            {
                "scenario_id",
                "partition",
                "repetitions",
                "scenario_manifest_sha256",
                "experiment_design_sha256",
                "admitted_public_files_sha256",
            },
            path,
        )
        scenario_id = require_identifier(row["scenario_id"], f"{path}.scenario_id")
        if scenario_id in scenario_repetitions:
            raise ContractError("exercise scenario IDs must be unique")
        require_enum(row["partition"], {"open", "sealed"}, f"{path}.partition")
        scenario_repetitions[scenario_id] = _integer(
            row["repetitions"], f"{path}.repetitions", minimum=1
        )
        for field in (
            "scenario_manifest_sha256",
            "experiment_design_sha256",
            "admitted_public_files_sha256",
        ):
            _digest(row[field], f"{path}.{field}")
        if (
            scenario_id not in SYNTHETIC_SCENARIO_MANIFEST_SHA256
            or row["scenario_manifest_sha256"]
            != SYNTHETIC_SCENARIO_MANIFEST_SHA256[scenario_id]
        ):
            raise ContractError("exercise scenario is not the frozen fixture")
        scenario_digests[scenario_id] = (
            str(row["scenario_manifest_sha256"]),
            str(row["experiment_design_sha256"]),
            str(row["admitted_public_files_sha256"]),
        )
    if set(scenario_repetitions) != set(SYNTHETIC_SCENARIO_MANIFEST_SHA256):
        raise ContractError("exercise must contain all four frozen scenarios")

    from .candidate import (
        AD_TESTING_SCRIPTS,
        _authenticate_materialized,
        _canonical_panel,
    )

    if str(AD_TESTING_SCRIPTS) not in sys.path:
        sys.path.insert(0, str(AD_TESTING_SCRIPTS))
    from audience_lab.complete_exposure import aggregate_complete_exposure
    from audience_lab.finalists import aggregate_finalists
    from audience_lab.responses import validate_job, validate_response

    panels = require_array(
        document["panel_bindings"],
        "exercise.panel_bindings",
        nonempty=True,
    )
    panel_by_ref: dict[str, dict[str, object]] = {}
    candidate_ids: set[str] = set()
    candidate_versions: set[tuple[str, str]] = set()
    base_panels = 0
    for index, raw in enumerate(panels):
        path = f"exercise.panel_bindings[{index}]"
        row = _object(
            raw,
            {
                "exercise_panel_ref",
                "panel_kind",
                "candidate_id",
                "panel_id",
                "panel_version",
                "panel_sha256",
                "candidate_binding_sha256",
                "proposal_sha256",
                "panel",
                "materialized_candidate",
                "candidate_seal",
            },
            path,
        )
        exercise_ref = require_identifier(
            row["exercise_panel_ref"], f"{path}.exercise_panel_ref"
        )
        if exercise_ref in panel_by_ref:
            raise ContractError("exercise panel references must be unique")
        require_enum(row["panel_kind"], {"base", "candidate"}, f"{path}.panel_kind")
        panel = _canonical_panel(row["panel"])
        if (
            row["panel_id"] != panel["panel_id"]
            or row["panel_version"] != panel["version"]
            or row["panel_sha256"] != sha256_json(panel)
        ):
            raise ContractError("exercise panel binding is stale")
        require_identifier(row["panel_id"], f"{path}.panel_id")
        require_string(row["panel_version"], f"{path}.panel_version")
        _digest(row["panel_sha256"], f"{path}.panel_sha256")
        if row["panel_kind"] == "base":
            base_panels += 1
            if any(
                row[field] is not None
                for field in (
                    "candidate_id",
                    "candidate_binding_sha256",
                    "proposal_sha256",
                    "materialized_candidate",
                    "candidate_seal",
                )
            ):
                raise ContractError("base exercise panel cannot claim candidate authority")
        else:
            candidate_id = require_identifier(
                row["candidate_id"], f"{path}.candidate_id"
            )
            if candidate_id in candidate_ids:
                raise ContractError("exercise candidate IDs must be unique")
            candidate_ids.add(candidate_id)
            version_key = (str(row["panel_id"]), str(row["panel_version"]))
            if version_key in candidate_versions:
                raise ContractError(
                    "exercise candidate panel ID/version pairs must be unique"
                )
            candidate_versions.add(version_key)
            _digest(
                row["candidate_binding_sha256"],
                f"{path}.candidate_binding_sha256",
            )
            _digest(row["proposal_sha256"], f"{path}.proposal_sha256")
            materialized = row["materialized_candidate"]
            if not isinstance(materialized, dict):
                raise ContractError("candidate exercise panel must carry its complete graph")
            candidate_seal = row["candidate_seal"]
            if not isinstance(candidate_seal, dict):
                raise ContractError(
                    "candidate exercise panel must carry its exact Task 6 seal"
                )
            validate_candidate_seal_envelope(
                {
                    "materialized_candidate": materialized,
                    **candidate_seal,
                }
            )
            binding, candidate_panel, _, proposal = _authenticate_materialized(
                materialized
            )
            if (
                candidate_id != binding["candidate_id"]
                or row["candidate_binding_sha256"]
                != binding["candidate_binding_sha256"]
                or row["proposal_sha256"] != proposal["proposal_sha256"]
                or canonical_json_bytes(candidate_panel)
                != canonical_json_bytes(panel)
            ):
                raise ContractError("candidate exercise authority graph is stale")
        expected_ref = _expected_exercise_panel_ref(
            panel_id=str(panel["panel_id"]),
            panel_version=str(panel["version"]),
            panel_kind=str(row["panel_kind"]),
            candidate_id=(
                None
                if row["panel_kind"] == "base"
                else str(row["candidate_id"])
            ),
        )
        if exercise_ref != expected_ref:
            raise ContractError(
                "exercise panel reference is not canonically derived"
            )
        panel_by_ref[exercise_ref] = row
    if base_panels != 1 or len(candidate_ids) < 2:
        raise ContractError("exercise requires one base and a nonempty plural candidate set")

    rosters = require_array(
        document["panel_rosters"],
        "exercise.panel_rosters",
        nonempty=True,
    )
    roster_members: dict[str, dict[str, dict[str, object]]] = {}
    for index, raw in enumerate(rosters):
        path = f"exercise.panel_rosters[{index}]"
        row = _object(
            raw,
            {
                "exercise_panel_ref",
                "panel_id",
                "panel_version",
                "panel_kind",
                "candidate_id",
                "members",
                "roster_sha256",
            },
            path,
        )
        supplied = _digest(row["roster_sha256"], f"{path}.roster_sha256")
        unhashed = deepcopy(row)
        unhashed["roster_sha256"] = None
        if supplied != sha256_json(unhashed):
            raise ContractError("exercise roster hash is stale")
        exercise_ref = require_identifier(
            row["exercise_panel_ref"], f"{path}.exercise_panel_ref"
        )
        if exercise_ref not in panel_by_ref or exercise_ref in roster_members:
            raise ContractError("exercise must have exactly one roster per panel ref")
        binding = panel_by_ref[exercise_ref]
        if any(
            row[field] != binding[field]
            for field in ("panel_id", "panel_version", "panel_kind", "candidate_id")
        ):
            raise ContractError("exercise roster panel binding is stale")
        members = require_array(row["members"], f"{path}.members", nonempty=True)
        member_by_id: dict[str, dict[str, object]] = {}
        panelists: set[str] = set()
        profiles: set[str] = set()
        panel_profiles = {
            str(profile["grounded_profile_id"]): profile
            for profile in binding["panel"]["grounded_context_profiles"]
        }
        for member_index, raw_member in enumerate(members):
            member_path = f"{path}.members[{member_index}]"
            member = _object(
                raw_member,
                {
                    "membership_id",
                    "panelist_id",
                    "grounded_profile_id",
                    "persona_archetype_id",
                    "segment_id",
                    "context_stratum_id",
                    "profile_snapshot",
                    "profile_snapshot_sha256",
                    "context_attribute_provenance",
                },
                member_path,
            )
            membership_id = require_identifier(
                member["membership_id"], f"{member_path}.membership_id"
            )
            panelist_id = require_identifier(
                member["panelist_id"], f"{member_path}.panelist_id"
            )
            profile_id = require_identifier(
                member["grounded_profile_id"],
                f"{member_path}.grounded_profile_id",
            )
            if (
                membership_id in member_by_id
                or panelist_id in panelists
                or profile_id in profiles
            ):
                raise ContractError(
                    "exercise roster membership, panelist, and profile IDs must be unique"
                )
            panelists.add(panelist_id)
            profiles.add(profile_id)
            source_profile = panel_profiles.get(profile_id)
            if source_profile is None or any(
                member[field] != source_profile[field]
                for field in (
                    "persona_archetype_id",
                    "segment_id",
                    "context_stratum_id",
                    "profile_snapshot",
                    "context_attribute_provenance",
                )
            ):
                raise ContractError("exercise roster is not an exact panel projection")
            if member["profile_snapshot_sha256"] != sha256_json(
                member["profile_snapshot"]
            ):
                raise ContractError("exercise roster snapshot hash is stale")
            member_by_id[membership_id] = member
        if profiles != set(panel_profiles):
            raise ContractError("exercise roster must cover every grounded profile")
        roster_members[exercise_ref] = member_by_id
    if set(roster_members) != set(panel_by_ref):
        raise ContractError("exercise rosters must cover every panel ref")

    expected_run_rows = {
        (scenario_id, repetition, exercise_ref)
        for scenario_id, repetitions in scenario_repetitions.items()
        for repetition in range(repetitions)
        for exercise_ref in panel_by_ref
    }
    jobs = require_array(
        document["panelist_jobs"], "exercise.panelist_jobs", nonempty=True
    )
    dispatches: set[str] = set()
    job_phases = {
        "complete-exposure": ("screening_response", "complete_exposure"),
        "maxdiff-screening": (
            "screening_response",
            "partial_exposure_maxdiff",
        ),
        "pairwise-boundary": (
            "boundary_response",
            "partial_exposure_maxdiff",
        ),
        "finalist-verbatim": ("finalist_response", "complete_exposure"),
    }
    job_rows: set[tuple[str, int, str, str, str]] = set()
    job_hashes_by_run: dict[tuple[str, int, str], set[str]] = defaultdict(set)
    for index, raw in enumerate(jobs):
        path = f"exercise.panelist_jobs[{index}]"
        row = _object(
            raw,
            {
                "dispatch_id",
                "phase",
                "scenario_id",
                "repetition",
                "exercise_panel_ref",
                "panel_id",
                "panel_version",
                "panelist_id",
                "membership_id",
                "worker_context_isolation",
                "job",
                "job_sha256",
            },
            path,
        )
        dispatch_id = require_identifier(row["dispatch_id"], f"{path}.dispatch_id")
        if dispatch_id in dispatches:
            raise ContractError("exercise dispatch IDs must be unique")
        dispatches.add(dispatch_id)
        scenario_id = require_identifier(row["scenario_id"], f"{path}.scenario_id")
        repetition = _integer(row["repetition"], f"{path}.repetition")
        exercise_ref = require_identifier(
            row["exercise_panel_ref"], f"{path}.exercise_panel_ref"
        )
        run_key = (scenario_id, repetition, exercise_ref)
        if run_key not in expected_run_rows:
            raise ContractError("exercise job is outside the frozen matrix")
        binding = panel_by_ref[exercise_ref]
        if (
            row["panel_id"] != binding["panel_id"]
            or row["panel_version"] != binding["panel_version"]
        ):
            raise ContractError("exercise job panel binding is stale")
        membership_id = require_identifier(
            row["membership_id"], f"{path}.membership_id"
        )
        member = roster_members[exercise_ref].get(membership_id)
        phase = require_enum(
            row["phase"], set(job_phases), f"{path}.phase"
        )
        if (
            member is None
            or row["panelist_id"] != member["panelist_id"]
            or row["worker_context_isolation"] != "isolated"
        ):
            raise ContractError("exercise job does not map one isolated roster worker")
        job = row["job"]
        if not isinstance(job, dict) or row["job_sha256"] != sha256_json(job):
            raise ContractError("exercise job hash is stale")
        errors = validate_job(job)
        if errors:
            raise ContractError("exercise carries an invalid job: " + "; ".join(errors))
        if (
            job["dispatch_id"] != dispatch_id
            or (job["record_type"], job["method"]) != job_phases[phase]
            or job["grounded_profile_id"] != member["grounded_profile_id"]
            or job["profile_snapshot_sha256"]
            != member["profile_snapshot_sha256"]
        ):
            raise ContractError("exercise job does not exactly bind its roster member")
        job_row = (
            scenario_id,
            repetition,
            exercise_ref,
            membership_id,
            phase,
        )
        if job_row in job_rows:
            raise ContractError("exercise has more than one worker for a membership")
        job_rows.add(job_row)
        job_hashes_by_run[run_key].add(str(row["job_sha256"]))
    expected_job_rows = {
        (
            scenario_id,
            repetition,
            exercise_ref,
            membership_id,
            phase,
        )
        for scenario_id, repetition, exercise_ref in expected_run_rows
        for membership_id in roster_members[exercise_ref]
        for phase in job_phases
    }
    if job_rows != expected_job_rows:
        raise ContractError("exercise jobs must cover every matrix roster membership")

    results = require_array(
        document["run_results"], "exercise.run_results", nonempty=True
    )
    observed_runs: set[tuple[str, int, str]] = set()
    result_keys = {
        "scenario_family_id",
        "scenario_id",
        "partition",
        "repetition",
        "exercise_panel_ref",
        "panel_id",
        "panel_version",
        "panel_kind",
        "candidate_id",
        "scenario_manifest_sha256",
        "experiment_design_sha256",
        "admitted_public_files_sha256",
        "assignment_plan",
        "assignment_plan_sha256",
        "capacity_plan",
        "capacity_plan_sha256",
        "job_sha256s",
        "adapter_outputs",
        "adapter_output_sha256s",
        "responses",
        "response_sha256s",
        "finalist_responses",
        "finalist_response_sha256s",
        "scoring_and_aggregation",
        "result_sha256",
    }
    for index, raw in enumerate(results):
        path = f"exercise.run_results[{index}]"
        row = _object(raw, result_keys, path)
        supplied = _digest(row["result_sha256"], f"{path}.result_sha256")
        unhashed = deepcopy(row)
        unhashed["result_sha256"] = None
        if supplied != sha256_json(unhashed):
            raise ContractError("exercise result hash is stale")
        scenario_id = require_identifier(row["scenario_id"], f"{path}.scenario_id")
        if row["scenario_family_id"] != scenario_id:
            raise ContractError("exercise scenario family ID must match scenario ID")
        repetition = _integer(row["repetition"], f"{path}.repetition")
        exercise_ref = require_identifier(
            row["exercise_panel_ref"], f"{path}.exercise_panel_ref"
        )
        run_key = (scenario_id, repetition, exercise_ref)
        if run_key not in expected_run_rows or run_key in observed_runs:
            raise ContractError("exercise result matrix row is missing or duplicated")
        observed_runs.add(run_key)
        binding = panel_by_ref[exercise_ref]
        if any(
            row[field] != binding[field]
            for field in ("panel_id", "panel_version", "panel_kind", "candidate_id")
        ):
            raise ContractError("exercise result panel binding is stale")
        expected_scenario_hashes = scenario_digests[scenario_id]
        if (
            row["scenario_manifest_sha256"],
            row["experiment_design_sha256"],
            row["admitted_public_files_sha256"],
        ) != expected_scenario_hashes:
            raise ContractError("exercise result scenario binding is stale")
        if row["assignment_plan_sha256"] != sha256_json(
            row["assignment_plan"]
        ):
            raise ContractError("exercise assignment plan hash is stale")
        if row["capacity_plan_sha256"] != sha256_json(row["capacity_plan"]):
            raise ContractError("exercise capacity plan hash is stale")
        capacity = row["capacity_plan"]
        member_count = len(roster_members[exercise_ref])
        if (
            not isinstance(capacity, Mapping)
            or capacity
            != {
                "screening_planned": 2 * member_count,
                "boundary_reserved": member_count,
                "finalist_reserved": member_count,
                "required_total": 4 * member_count,
                "ceiling": 4 * member_count,
                "ceiling_satisfied": True,
            }
        ):
            raise ContractError("exercise capacity/reserve math is not exact")
        job_hashes = [
            _digest(value, f"{path}.job_sha256s[{hash_index}]")
            for hash_index, value in enumerate(
                require_array(
                    row["job_sha256s"], f"{path}.job_sha256s", nonempty=True
                )
            )
        ]
        if len(job_hashes) != len(set(job_hashes)):
            raise ContractError("exercise result job hashes must be unique")
        if set(job_hashes) != job_hashes_by_run[run_key]:
            raise ContractError("exercise result job hash set is stale")
        for values_field, hashes_field in (
            ("adapter_outputs", "adapter_output_sha256s"),
            ("responses", "response_sha256s"),
            ("finalist_responses", "finalist_response_sha256s"),
        ):
            values = require_array(row[values_field], f"{path}.{values_field}", nonempty=True)
            hashes = require_array(row[hashes_field], f"{path}.{hashes_field}", nonempty=True)
            if hashes != [sha256_json(value) for value in values]:
                raise ContractError(f"exercise {values_field} hashes are stale")
        run_job_rows = [
            job_row
            for job_row in jobs
            if (
                job_row["scenario_id"],
                job_row["repetition"],
                job_row["exercise_panel_ref"],
            )
            == run_key
        ]
        run_jobs = [job_row["job"] for job_row in run_job_rows]
        expected_assignment = _exercise_assignment_projection(run_jobs)
        if canonical_json_bytes(row["assignment_plan"]) != (
            canonical_json_bytes(expected_assignment)
        ):
            raise ContractError(
                "exercise assignment plan is not the exact job projection"
            )
        responses = row["responses"]
        if len(run_jobs) != len(responses):
            raise ContractError("exercise response count must equal isolated jobs")
        jobs_by_response = {job["response_id"]: job for job in run_jobs}
        jobs_by_dispatch = {job["dispatch_id"]: job for job in run_jobs}
        for response in responses:
            job = jobs_by_response.get(response.get("response_id"))
            errors = validate_response(response, job)
            if job is None or errors:
                raise ContractError(
                    "exercise carries an invalid job-bound response"
                    + (": " + "; ".join(errors) if errors else "")
                )
        if set(jobs_by_dispatch) != {
            output.get("dispatch_id") for output in row["adapter_outputs"]
        }:
            raise ContractError(
                "exercise adapter outputs must exactly cover every job"
            )
        for output in row["adapter_outputs"]:
            job = jobs_by_dispatch.get(output.get("dispatch_id"))
            ranking = output.get("ranking")
            if (
                job is None
                or set(output)
                != {
                    "adapter_id",
                    "adapter_version",
                    "dispatch_id",
                    "tie_rule",
                    "ranking",
                }
                or output["adapter_id"]
                != "frozen-synthetic-panelist-response"
                or output["adapter_version"] != "1.0.0"
                or output["tie_rule"]
                != "score-descending-creative-id-ascending"
                or not isinstance(ranking, list)
                or any(
                    not isinstance(item, Mapping)
                    or set(item) != {"position", "creative_id", "score"}
                    or item["position"] != position
                    or isinstance(item["score"], bool)
                    or not isinstance(item["score"], int)
                    for position, item in enumerate(ranking, 1)
                )
                or [item["creative_id"] for item in ranking]
                != list(dict.fromkeys(item["creative_id"] for item in ranking))
                or {item["creative_id"] for item in ranking}
                != set(job["variation_ids"])
            ):
                raise ContractError(
                    "exercise adapter output is not an exact job projection"
                )
        finalist_responses = [
            response
            for response in responses
            if response["record_type"] == "finalist_response"
        ]
        if canonical_json_bytes(row["finalist_responses"]) != (
            canonical_json_bytes(finalist_responses)
        ):
            raise ContractError(
                "exercise finalist response projection is not exact"
            )
        scoring = row["scoring_and_aggregation"]
        if (
            not isinstance(scoring, Mapping)
            or set(scoring)
            != {
                "scoring_inputs",
                "complete_exposure",
                "maxdiff",
                "pairwise_boundary",
                "finalist_aggregation",
                "verbatim_projection",
                "numerical_binding",
                "scoring_sha256",
            }
        ):
            raise ContractError("exercise scoring graph is not closed")
        scoring_copy = deepcopy(dict(scoring))
        scoring_hash = scoring_copy["scoring_sha256"]
        scoring_copy["scoring_sha256"] = None
        if scoring_hash != sha256_json(scoring_copy):
            raise ContractError("exercise scoring graph hash is stale")
        inputs = _object(
            scoring["scoring_inputs"],
            {
                "complete_exposure",
                "maxdiff",
                "pairwise_boundary",
                "finalist_aggregation",
            },
            f"{path}.scoring_and_aggregation.scoring_inputs",
        )
        responses_by_phase = {
            "complete_exposure": [
                response
                for response in responses
                if response["record_type"] == "screening_response"
                and response["method"] == "complete_exposure"
            ],
            "maxdiff": [
                response
                for response in responses
                if response["record_type"] == "screening_response"
                and response["method"] == "partial_exposure_maxdiff"
            ],
            "pairwise_boundary": [
                response
                for response in responses
                if response["record_type"] == "boundary_response"
            ],
            "finalist_aggregation": finalist_responses,
        }
        expected_study_id = (
            f"{study_binding['study_id']}-{scenario_id}-"
            f"r{repetition}-{exercise_ref}"
        )
        if any(job.get("study_id") != expected_study_id for job in run_jobs):
            raise ContractError(
                "exercise job study identity is not canonically derived"
            )
        complete_jobs = [
            job
            for job in run_jobs
            if job["record_type"] == "screening_response"
            and job["method"] == "complete_exposure"
        ]
        maxdiff_jobs = [
            job
            for job in run_jobs
            if job["record_type"] == "screening_response"
            and job["method"] == "partial_exposure_maxdiff"
        ]
        boundary_jobs = [
            job
            for job in run_jobs
            if job["record_type"] == "boundary_response"
        ]
        finalist_jobs = [
            job
            for job in run_jobs
            if job["record_type"] == "finalist_response"
        ]
        creative_ids = sorted(
            {
                str(creative_id)
                for job in complete_jobs
                for creative_id in job["variation_ids"]
            }
        )
        if (
            len(creative_ids) != 4
            or any(
                set(job["variation_ids"]) != set(creative_ids)
                for job in (*complete_jobs, *maxdiff_jobs)
            )
        ):
            raise ContractError(
                "exercise screening jobs lost the frozen creative roster"
            )
        segment_counts = Counter(
            str(response["segment_id"])
            for response in responses_by_phase["complete_exposure"]
        )
        response_total = sum(segment_counts.values())
        segment_weights = {
            segment_id: count / response_total
            for segment_id, count in sorted(segment_counts.items())
        }
        seed = SYNTHETIC_SCENARIO_SEED[scenario_id] + repetition
        ranking_counts: dict[str, int] = defaultdict(int)
        for response in responses_by_phase["complete_exposure"]:
            output = jobs_by_dispatch.get(
                response["reviewer_dispatch_id"]
            )
            adapter_output = next(
                (
                    item
                    for item in row["adapter_outputs"]
                    if item["dispatch_id"]
                    == response["reviewer_dispatch_id"]
                ),
                None,
            )
            if output is None or adapter_output is None:
                raise ContractError(
                    "exercise complete-exposure ranking authority is missing"
                )
            for ranking in adapter_output["ranking"]:
                ranking_counts[str(ranking["creative_id"])] += (
                    len(creative_ids) - int(ranking["position"])
                )
        expected_finalist_ids = sorted(
            creative_ids,
            key=lambda creative_id: (
                -ranking_counts[creative_id],
                creative_id,
            ),
        )[:2]
        if any(
            job["variation_ids"] != expected_finalist_ids
            for job in finalist_jobs
        ):
            raise ContractError(
                "exercise finalist jobs are not derived from screening"
            )
        finalist_manifest = {
            "study_id": expected_study_id,
            "method": "complete_exposure",
            "requested_shortlist_size": 2,
            "outputs": {
                "creative_asset_hashes": {
                    creative_id: (
                        "sha256:"
                        + hashlib.sha256(
                            creative_id.encode("utf-8")
                        ).hexdigest()
                    )
                    for creative_id in creative_ids
                }
            },
        }
        screening_result = {
            "study_id": expected_study_id,
            "method": "complete_exposure",
            "validity_status": "valid",
            "selection_status": "resolved",
            "proposed_finalist_ids": expected_finalist_ids,
        }
        finalist_approval = {
            "study_id": expected_study_id,
            "method": "complete_exposure",
            "approved_finalist_ids": expected_finalist_ids,
            "roster_decision": {
                "status": "approved",
                "approved_at": "2026-07-30T00:00:00Z",
                "approved_by": "synthetic-sandbox-harness",
                "override": False,
                "changed_after_saliency_reveal": False,
            },
        }
        expected_inputs = {
            "complete_exposure": {
                "study_id": expected_study_id,
                "creative_ids": creative_ids,
                "top_k": 2,
                "segment_weights": segment_weights,
                "seed": seed,
                "response_sha256s": [
                    sha256_json(response)
                    for response in responses_by_phase[
                        "complete_exposure"
                    ]
                ],
            },
            "maxdiff": {
                "config": {
                    "penalty_lambda": 0.1,
                    "optimizer_tolerance": 1e-8,
                    "bootstrap_count": 2000,
                    "successful_fit_floor": 0.95,
                    "clear_finalist_threshold": 0.9,
                    "clear_non_finalist_threshold": 0.1,
                    "seed": seed,
                },
                "creative_ids": creative_ids,
                "segment_weights": segment_weights,
                "response_sha256s": [
                    sha256_json(response)
                    for response in responses_by_phase["maxdiff"]
                ],
            },
            "pairwise_boundary": {
                "config": {
                    "tie_parameter": 0.4,
                    "penalty_lambda": 0.1,
                    "optimizer_tolerance": 1e-8,
                    "bootstrap_count": 20,
                    "successful_fit_floor": 0.95,
                    "seed": seed,
                },
                "candidate_ids": creative_ids,
                "target_count": 2,
                "segment_weights": segment_weights,
                "boundary_jobs_per_wave": len(boundary_jobs),
                "boundary_waves_max": 1,
                "boundary_reserved": len(boundary_jobs),
                "available_boundary_reserve": len(boundary_jobs),
                "finalist_reserved": len(finalist_jobs),
                "response_sha256s": [
                    sha256_json(response)
                    for response in responses_by_phase[
                        "pairwise_boundary"
                    ]
                ],
            },
            "finalist_aggregation": {
                "manifest": finalist_manifest,
                "screening_result": screening_result,
                "approval": finalist_approval,
                "response_sha256s": [
                    sha256_json(response)
                    for response in responses_by_phase[
                        "finalist_aggregation"
                    ]
                ],
            },
        }
        if canonical_json_bytes(inputs) != canonical_json_bytes(
            expected_inputs
        ):
            raise ContractError(
                "exercise scoring inputs are not canonically derived"
            )
        complete_input = expected_inputs["complete_exposure"]
        expected_complete = aggregate_complete_exposure(
            responses_by_phase["complete_exposure"],
            study_id=str(complete_input["study_id"]),
            creative_ids=list(complete_input["creative_ids"]),
            top_k=int(complete_input["top_k"]),
            segment_weights=dict(complete_input["segment_weights"]),
            seed=int(complete_input["seed"]),
        )
        if canonical_json_bytes(scoring["complete_exposure"]) != (
            canonical_json_bytes(expected_complete)
        ):
            raise ContractError(
                "exercise complete-exposure aggregation is not replayable"
            )
        finalist_input = expected_inputs["finalist_aggregation"]
        expected_finalist = aggregate_finalists(
            finalist_input["manifest"],
            finalist_input["screening_result"],
            finalist_input["approval"],
            finalist_responses,
        )
        if canonical_json_bytes(scoring["finalist_aggregation"]) != (
            canonical_json_bytes(expected_finalist)
        ):
            raise ContractError(
                "exercise finalist aggregation is not replayable"
            )
        maxdiff = scoring["maxdiff"]
        if not isinstance(maxdiff, Mapping) or set(maxdiff) != {
            "utilities",
            "ranked_ids",
            "success",
            "connected",
            "identified",
            "converged",
            "loss",
            "projected_gradient_norm",
            "iterations",
            "message",
            "observation_count",
            "creative_count",
        }:
            raise ContractError("exercise MaxDiff output schema is not closed")
        boundary = scoring["pairwise_boundary"]
        if not isinstance(boundary, Mapping) or set(boundary) != {
            "status",
            "status_reasons",
            "estimand",
            "stability_diagnostic",
            "boundary_candidate_ids",
            "frozen_clear_finalist_ids",
            "frozen_clear_non_finalist_ids",
            "selected_boundary_ids",
            "proposed_finalist_ids",
            "utilities",
            "ranked_ids",
            "conditional_inclusion_frequencies",
            "classifications",
            "model_diagnostics",
            "decision_audit",
            "interpretation_limits",
        }:
            raise ContractError(
                "exercise pairwise boundary output schema is not closed"
            )
        numerical = _object(
            scoring["numerical_binding"],
            {
                "maxdiff_input_sha256",
                "maxdiff_output_sha256",
                "pairwise_input_sha256",
                "pairwise_output_sha256",
                "dependency_complete_recomputation_required",
            },
            f"{path}.scoring_and_aggregation.numerical_binding",
        )
        if (
            numerical["maxdiff_input_sha256"]
            != sha256_json(inputs["maxdiff"])
            or numerical["maxdiff_output_sha256"] != sha256_json(maxdiff)
            or numerical["pairwise_input_sha256"]
            != sha256_json(inputs["pairwise_boundary"])
            or numerical["pairwise_output_sha256"] != sha256_json(boundary)
            or numerical["dependency_complete_recomputation_required"]
            is not True
        ):
            raise ContractError(
                "exercise numerical output binding is stale"
            )
        verbatim = scoring["verbatim_projection"]
        expected_reactions = [
            deepcopy(reaction)
            for response in responses
            for reaction in response.get(
                "per_creative_reactions",
                response.get("finalist_reviews", []),
            )
        ]
        if (
            not isinstance(verbatim, Mapping)
            or set(verbatim)
            != {
                "capture",
                "exact_response_sha256",
                "reaction_records",
            }
            or verbatim.get("capture")
            != "frozen_adapter_ranking_projection"
            or verbatim.get("exact_response_sha256")
            != row["response_sha256s"]
            or canonical_json_bytes(verbatim.get("reaction_records"))
            != canonical_json_bytes(expected_reactions)
        ):
            raise ContractError("exercise verbatim projection lost response identity")
    if observed_runs != expected_run_rows:
        raise ContractError("exercise results must cover the complete frozen matrix")

    authority = _object(
        document["production_authority"],
        {
            "package_created",
            "resolution_created",
            "registration_permitted",
            "activation_permitted",
            "active_panel_mutation_permitted",
        },
        "exercise.production_authority",
    )
    if any(value is not False for value in authority.values()):
        raise ContractError("exercise cannot grant production authority")
    require_string_array(
        document["limitations"], "exercise.limitations", nonempty=True
    )
    return _require_self_hash(document, "exercise_sha256", "exercise")
