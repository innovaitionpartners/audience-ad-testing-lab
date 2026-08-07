"""Exercise standalone base and sandbox-candidate panels on fictional scenarios.

This module is an experimental orchestration boundary.  It deliberately uses
the existing low-level Ad Testing capacity, assignment, job, response, scoring,
comparison, finalist, complete-exposure, and aggregation functions without
creating a production package, resolution, dispatch authority, registration,
activation, or active-panel mutation.
"""

from __future__ import annotations

import ast
from collections import Counter, defaultdict
from collections.abc import Callable, Mapping, Sequence
from copy import deepcopy
from dataclasses import asdict
import hashlib
import json
from pathlib import Path
import stat
import sys

from ...common import (
    ContractError,
    canonical_json_bytes,
    require_identifier,
    require_timestamp,
    sha256_json,
)
from .candidate import _authenticate_materialized
from .contracts import (
    EXERCISE_VERSION,
    SYNTHETIC_SCENARIO_MANIFEST_SHA256,
    SYNTHETIC_SCENARIO_REGISTRY,
    _exercise_assignment_projection,
    validate_creative_attribute_registry,
    validate_candidate_seal_envelope,
    validate_study_manifest,
    validate_synthetic_exercise,
)
from .synthetic_response_adapter import (
    synthetic_panelist_response as _synthetic_panelist_response,
)


_SKILLS_ROOT = Path(__file__).resolve(strict=True).parents[5]
_AD_TESTING_SCRIPTS = _SKILLS_ROOT / "audience-ad-testing-lab" / "scripts"
_ADAPTER_SOURCE = (
    Path(__file__).resolve(strict=True).parent / "synthetic_response_adapter.py"
)
_PINNED_RUNTIME = {"numpy": "2.4.2", "scipy": "1.17.0"}
_ATTEMPT_POLICY = {
    "capture": "frozen_adapter_ranking_projection",
    "reaction_text": "Deterministic synthetic machinery probe.",
}
_BEHAVIOR_FIELDS = (
    "anxieties",
    "decision_context",
    "motivations",
    "proof_needs",
    "role_context",
)
_FORBIDDEN_ADAPTER_NAMES = {
    "__import__",
    "environ",
    "eval",
    "exec",
    "getenv",
    "open",
    "outcome",
    "pathlib",
    "requests",
    "socket",
    "subprocess",
    "urllib",
}
_FINALIST_RUBRIC_KEYS = (
    "attention_potential",
    "comprehension",
    "credibility",
    "friction",
    "motivation",
    "offer_appeal",
    "overall",
    "relevance",
)


authenticate_candidate_seal_envelope = validate_candidate_seal_envelope


class ExerciseDependencyUnavailable(ContractError):
    """The exact pinned optimizer runtime is unavailable."""


class ExerciseSourceIsolationFailure(ContractError):
    """The frozen exercise source cannot be authenticated in isolation."""


def _digest_bytes(value: bytes) -> str:
    return "sha256:" + hashlib.sha256(value).hexdigest()


def _copy_json(value: object, path: str) -> object:
    try:
        return json.loads(canonical_json_bytes(value))
    except (TypeError, ValueError, OverflowError, RecursionError) as exc:
        raise ContractError(f"{path} must be finite JSON-shaped data") from exc


def _self_hash(
    value: Mapping[str, object],
    field: str,
    path: str,
) -> dict[str, object]:
    result = deepcopy(dict(value))
    supplied = result.get(field)
    result[field] = None
    if supplied != sha256_json(result):
        raise ContractError(f"{path}.{field} is stale")
    result[field] = supplied
    return result


def _assert_adapter_ast(raw: bytes) -> str:
    try:
        source = raw.decode("utf-8")
        tree = ast.parse(source, filename="synthetic_response_adapter.py")
    except (UnicodeDecodeError, SyntaxError) as exc:
        raise ContractError("frozen adapter source is not valid canonical Python") from exc
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            raise ContractError("frozen adapter cannot import runtime authority")
        if isinstance(node, ast.ImportFrom) and node.module != "__future__":
            raise ContractError("frozen adapter cannot import runtime authority")
        if isinstance(node, ast.Name) and node.id.casefold() in _FORBIDDEN_ADAPTER_NAMES:
            raise ContractError(
                f"frozen adapter contains forbidden authority name {node.id!r}"
            )
        if isinstance(node, ast.Attribute) and node.attr.casefold() in _FORBIDDEN_ADAPTER_NAMES:
            raise ContractError(
                f"frozen adapter contains forbidden authority attribute {node.attr!r}"
            )
    return _digest_bytes(ast.dump(tree, include_attributes=False).encode("utf-8"))


def authenticate_frozen_adapter_source(
    study_manifest: Mapping[str, object],
) -> dict[str, object]:
    """Authenticate the exact Task 2 adapter bytes without caller override."""

    manifest = validate_study_manifest(study_manifest)
    try:
        source_path = _ADAPTER_SOURCE.resolve(strict=True)
        first_stat = source_path.lstat()
        if stat.S_ISLNK(first_stat.st_mode) or not stat.S_ISREG(first_stat.st_mode):
            raise ContractError("frozen adapter source must be one regular file")
        first = source_path.read_bytes()
        middle_stat = source_path.stat(follow_symlinks=False)
        second = source_path.read_bytes()
        final_stat = source_path.stat(follow_symlinks=False)
        identities = {
            (first_stat.st_dev, first_stat.st_ino),
            (middle_stat.st_dev, middle_stat.st_ino),
            (final_stat.st_dev, final_stat.st_ino),
        }
        if len(identities) != 1 or first != second:
            raise ContractError(
                "frozen adapter source changed across authenticated reads"
            )
        if b"\r" in first or not first.endswith(b"\n"):
            raise ContractError(
                "frozen adapter source must use canonical UTF-8/LF bytes"
            )
        source_sha256 = _digest_bytes(first)
        adapter = manifest["synthetic_response_adapter"]
        if not isinstance(adapter, Mapping):
            raise ContractError("study manifest adapter binding is unavailable")
        if adapter["source_sha256"] != source_sha256:
            raise ContractError(
                "frozen adapter bytes do not match the study manifest"
            )
        ast_sha256 = _assert_adapter_ast(first)
    except ExerciseSourceIsolationFailure:
        raise
    except (ContractError, OSError) as exc:
        raise ExerciseSourceIsolationFailure(str(exc)) from exc
    return {
        "adapter_id": adapter["adapter_id"],
        "adapter_version": adapter["version"],
        "source_sha256": source_sha256,
        "first_read_sha256": _digest_bytes(first),
        "second_read_sha256": _digest_bytes(second),
        "ast_sha256": ast_sha256,
        "feature_allowlist": list(adapter["feature_allowlist"]),
        "deterministic_tie_rule": adapter["deterministic_tie_rule"],
    }


def _adapter_callable(
    study_manifest: Mapping[str, object],
) -> tuple[Callable[..., dict[str, object]], dict[str, object]]:
    binding = authenticate_frozen_adapter_source(study_manifest)
    if not callable(_synthetic_panelist_response):
        raise ExerciseSourceIsolationFailure(
            "authenticated adapter callable is unavailable"
        )
    return _synthetic_panelist_response, binding


def runtime_dependencies_available() -> bool:
    """Return whether the exact pinned NumPy/SciPy runtime can be resolved."""

    try:
        import numpy
        import scipy
    except ImportError:
        return False
    return (
        numpy.__version__ == _PINNED_RUNTIME["numpy"]
        and scipy.__version__ == _PINNED_RUNTIME["scipy"]
    )


def _require_runtime_dependencies() -> None:
    if not runtime_dependencies_available():
        raise ExerciseDependencyUnavailable(
            "synthetic exercise requires exact pinned numpy==2.4.2 and scipy==1.17.0"
        )


def _canonical_panel(value: object) -> dict[str, object]:
    if str(_AD_TESTING_SCRIPTS) not in sys.path:
        sys.path.insert(0, str(_AD_TESTING_SCRIPTS))
    try:
        from audience_lab.audience_research_v3 import validate_saved_panel_v3

        result = validate_saved_panel_v3(value)
    except ValueError as exc:
        raise ContractError(str(exc)) from exc
    if not isinstance(result, dict):
        raise ContractError("standalone saved-panel validator returned invalid data")
    return result


def _regular_tree_files(root: Path) -> tuple[dict[str, bytes], set[str]]:
    rows: dict[str, bytes] = {}
    directories: set[str] = set()
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root).as_posix()
        value = path.lstat()
        if stat.S_ISLNK(value.st_mode):
            raise ContractError("public scenario tree cannot contain symlinks")
        if path.is_dir():
            directories.add(relative)
            continue
        if not stat.S_ISREG(value.st_mode):
            raise ContractError("public scenario tree must contain only regular files")
        rows[relative] = path.read_bytes()
    return rows, directories


def _require_plain_directory(path: Path, label: str) -> Path:
    try:
        value = path.lstat()
    except OSError as exc:
        raise ContractError(f"{label} is unavailable") from exc
    if stat.S_ISLNK(value.st_mode) or not stat.S_ISDIR(value.st_mode):
        raise ContractError(f"{label} must be one non-symlinked directory")
    return path


def load_public_scenario_inputs(root: Path) -> list[dict[str, object]]:
    """Load only exact manifest-bound public scenario bytes."""

    source = _require_plain_directory(
        Path(root).absolute(), "public scenarios root"
    )
    expected_partitions = {"open", "sealed"}
    if {child.name for child in source.iterdir()} != expected_partitions:
        raise ContractError(
            "public scenarios root must contain exactly open and sealed"
        )
    expected_by_partition = {
        partition: {
            scenario_id
            for scenario_id, binding in SYNTHETIC_SCENARIO_REGISTRY.items()
            if binding["partition"] == partition
        }
        for partition in expected_partitions
    }
    scenario_directories: list[Path] = []
    for partition in sorted(expected_partitions):
        partition_root = _require_plain_directory(
            source / partition, f"public {partition} partition"
        )
        children = {child.name: child for child in partition_root.iterdir()}
        if set(children) != expected_by_partition[partition]:
            raise ContractError(
                f"public {partition} partition has an unexpected scenario tree"
            )
        for scenario_id in sorted(children):
            scenario_directories.append(
                _require_plain_directory(
                    children[scenario_id],
                    f"public scenario {scenario_id}",
                )
            )
    rows: list[dict[str, object]] = []
    seen: set[str] = set()
    for directory in scenario_directories:
        files, directories = _regular_tree_files(directory)
        manifest_raw = files.get("scenario-manifest.json")
        if manifest_raw is None:
            raise ContractError("public scenario is missing scenario-manifest.json")
        try:
            manifest = json.loads(manifest_raw)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ContractError("public scenario manifest is not JSON") from exc
        if canonical_json_bytes(manifest) != manifest_raw:
            raise ContractError("public scenario manifest is not canonical JSON")
        if not isinstance(manifest, Mapping):
            raise ContractError("public scenario manifest must be an object")
        checked_manifest = _self_hash(
            manifest, "manifest_sha256", "public_scenario_manifest"
        )
        scenario_binding = checked_manifest.get("scenario_binding")
        if not isinstance(scenario_binding, Mapping):
            raise ContractError("public scenario manifest has no scenario binding")
        scenario_id = require_identifier(
            scenario_binding.get("scenario_id"), "scenario_id"
        )
        if scenario_id in seen:
            raise ContractError("public scenarios must have unique scenario IDs")
        seen.add(scenario_id)
        if (
            scenario_id not in SYNTHETIC_SCENARIO_MANIFEST_SHA256
            or checked_manifest["manifest_sha256"]
            != SYNTHETIC_SCENARIO_MANIFEST_SHA256[scenario_id]
        ):
            raise ContractError("public scenario manifest is not the frozen fixture")
        public_bindings = checked_manifest.get("public_file_bindings")
        if not isinstance(public_bindings, list) or not public_bindings:
            raise ContractError("public scenario file bindings must be nonempty")
        expected_paths = {"scenario-manifest.json"}
        admitted: list[dict[str, object]] = [
            {
                "path": "scenario-manifest.json",
                "byte_count": len(manifest_raw),
                "raw_bytes_sha256": _digest_bytes(manifest_raw),
            }
        ]
        for index, raw_binding in enumerate(public_bindings):
            if not isinstance(raw_binding, Mapping) or set(raw_binding) != {
                "path",
                "byte_count",
                "raw_bytes_sha256",
            }:
                raise ContractError(
                    f"public_file_bindings[{index}] has an invalid closed shape"
                )
            relative = raw_binding["path"]
            if (
                not isinstance(relative, str)
                or not relative
                or relative.startswith("/")
                or ".." in Path(relative).parts
            ):
                raise ContractError("public scenario binding path is unsafe")
            raw_file = files.get(relative)
            if raw_file is None:
                raise ContractError("public scenario binding names a missing file")
            if (
                raw_binding["byte_count"] != len(raw_file)
                or raw_binding["raw_bytes_sha256"] != _digest_bytes(raw_file)
            ):
                raise ContractError("public scenario file binding is stale")
            expected_paths.add(relative)
            admitted.append(dict(raw_binding))
        if set(files) != expected_paths:
            raise ContractError(
                "public scenario tree must contain the exact manifest-bound files"
            )
        expected_directories = {
            parent.as_posix()
            for relative in expected_paths
            for parent in Path(relative).parents
            if parent != Path(".")
        }
        if directories != expected_directories:
            raise ContractError(
                "public scenario tree must contain the exact manifest-bound directories"
            )
        design_raw = files.get("experiment-design.json")
        if design_raw is None:
            raise ContractError("public scenario is missing experiment-design.json")
        try:
            design = json.loads(design_raw)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ContractError("experiment design is not JSON") from exc
        if canonical_json_bytes(design) != design_raw or not isinstance(design, Mapping):
            raise ContractError("experiment design must be one canonical JSON object")
        checked_design = _self_hash(design, "design_sha256", "experiment_design")
        rows.append(
            {
                "scenario_manifest": checked_manifest,
                "experiment_design": checked_design,
                "admitted_public_files": sorted(admitted, key=lambda row: row["path"]),
            }
        )
    if seen != set(SYNTHETIC_SCENARIO_MANIFEST_SHA256):
        raise ContractError("public scenarios root must contain all four frozen scenarios")
    return sorted(
        rows,
        key=lambda row: row["scenario_manifest"]["scenario_binding"]["scenario_id"],
    )


def _validate_public_scenario_inputs(
    value: Sequence[Mapping[str, object]],
    study_manifest: Mapping[str, object],
) -> list[dict[str, object]]:
    if (
        not isinstance(value, Sequence)
        or isinstance(value, (str, bytes))
        or not value
    ):
        raise ContractError("public_scenario_inputs must be a nonempty array")
    manifest_families = {
        str(row["scenario_id"]): row
        for row in study_manifest["scenario_families"]
    }
    result: list[dict[str, object]] = []
    seen: set[str] = set()
    for index, raw in enumerate(value):
        if not isinstance(raw, Mapping) or set(raw) != {
            "scenario_manifest",
            "experiment_design",
            "admitted_public_files",
        }:
            raise ContractError(
                f"public_scenario_inputs[{index}] is not the closed public envelope"
            )
        scenario_manifest = _self_hash(
            raw["scenario_manifest"],
            "manifest_sha256",
            f"public_scenario_inputs[{index}].scenario_manifest",
        )
        design = _self_hash(
            raw["experiment_design"],
            "design_sha256",
            f"public_scenario_inputs[{index}].experiment_design",
        )
        scenario_id = str(scenario_manifest["scenario_binding"]["scenario_id"])
        if scenario_id in seen or scenario_id not in manifest_families:
            raise ContractError("public scenarios are duplicated or outside the study")
        seen.add(scenario_id)
        family = manifest_families[scenario_id]
        expected_scenario_binding = {
            "dgp_id": family["dgp_id"],
            "dgp_version": family["dgp_version"],
            "parameters_sha256": family["parameters"]["parameters_sha256"],
            "repetitions": family["repetitions"],
            "scenario_id": family["scenario_id"],
            "seed": family["seed"],
            "study_id": study_manifest["study_id"],
            "study_manifest_sha256": study_manifest["manifest_sha256"],
        }
        for field, expected_value in expected_scenario_binding.items():
            if scenario_manifest["scenario_binding"][field] != expected_value:
                raise ContractError("public scenario does not match study manifest")
        if (
            scenario_manifest["manifest_sha256"]
            != SYNTHETIC_SCENARIO_MANIFEST_SHA256[scenario_id]
            or design["scenario_binding"] != scenario_manifest["scenario_binding"]
            or design["study_manifest_binding"]
            != scenario_manifest["study_manifest_binding"]
        ):
            raise ContractError("scenario manifest and experiment design are not bound")
        design_binding = next(
            (
                row
                for row in scenario_manifest["public_file_bindings"]
                if row["path"] == "experiment-design.json"
            ),
            None,
        )
        design_bytes = canonical_json_bytes(design)
        if not isinstance(design_binding, Mapping) or (
            design_binding["raw_bytes_sha256"] != _digest_bytes(design_bytes)
            or design_binding["byte_count"] != len(design_bytes)
        ):
            raise ContractError("experiment design bytes are not scenario-bound")
        admitted = raw["admitted_public_files"]
        if (
            not isinstance(admitted, list)
            or admitted
            != sorted(admitted, key=lambda row: row["path"])
            or any(
                not isinstance(row, Mapping)
                or set(row) != {"path", "byte_count", "raw_bytes_sha256"}
                for row in admitted
            )
        ):
            raise ContractError("admitted public files are not a closed canonical list")
        expected = [
            {
                "path": "scenario-manifest.json",
                "byte_count": len(canonical_json_bytes(scenario_manifest)),
                "raw_bytes_sha256": _digest_bytes(
                    canonical_json_bytes(scenario_manifest)
                ),
            },
            *[dict(row) for row in scenario_manifest["public_file_bindings"]],
        ]
        if admitted != sorted(expected, key=lambda row: row["path"]):
            raise ContractError("admitted public files are not exact manifest bytes")
        result.append(
            {
                "scenario_manifest": scenario_manifest,
                "experiment_design": design,
                "admitted_public_files": [dict(row) for row in admitted],
            }
        )
    if seen != set(manifest_families):
        raise ContractError("public scenarios must cover every study scenario")
    return sorted(
        result,
        key=lambda row: row["scenario_manifest"]["scenario_binding"]["scenario_id"],
    )


def _adapter_output_ranking(
    adapter_output: Mapping[str, object],
    job: Mapping[str, object],
) -> list[str]:
    if set(adapter_output) != {
        "adapter_id",
        "adapter_version",
        "dispatch_id",
        "tie_rule",
        "ranking",
    }:
        raise ContractError("adapter output is not closed")
    if (
        adapter_output["adapter_id"] != "frozen-synthetic-panelist-response"
        or adapter_output["adapter_version"] != "1.0.0"
        or adapter_output["dispatch_id"] != job["dispatch_id"]
        or adapter_output["tie_rule"]
        != "score-descending-creative-id-ascending"
    ):
        raise ContractError("adapter output authority does not match the job")
    rows = adapter_output["ranking"]
    if not isinstance(rows, list) or not rows:
        raise ContractError("adapter output ranking must be nonempty")
    ranking: list[str] = []
    for index, row in enumerate(rows, 1):
        if (
            not isinstance(row, Mapping)
            or set(row) != {"position", "creative_id", "score"}
            or row["position"] != index
            or not isinstance(row["creative_id"], str)
            or isinstance(row["score"], bool)
            or not isinstance(row["score"], int)
        ):
            raise ContractError("adapter output ranking row is invalid")
        ranking.append(row["creative_id"])
    assigned = job["variation_ids"]
    if len(ranking) != len(set(ranking)) or set(ranking) != set(assigned):
        raise ContractError("adapter ranking must exactly cover assigned creatives")
    return ranking


def project_adapter_output_to_ad_testing_response(
    *,
    adapter_output: Mapping[str, object],
    validated_job: Mapping[str, object],
    frozen_attempt_policy: Mapping[str, object],
) -> dict[str, object]:
    """Project one ranking envelope into a full unchanged response contract."""

    if str(_AD_TESTING_SCRIPTS) not in sys.path:
        sys.path.insert(0, str(_AD_TESTING_SCRIPTS))
    from audience_lab.responses import validate_job, validate_response

    job = _copy_json(validated_job, "validated_job")
    if not isinstance(job, dict):
        raise ContractError("validated_job must be an object")
    job_errors = validate_job(job)
    if job_errors:
        raise ContractError("invalid Ad Testing job: " + "; ".join(job_errors))
    if (
        not isinstance(frozen_attempt_policy, Mapping)
        or dict(frozen_attempt_policy) != _ATTEMPT_POLICY
    ):
        raise ContractError("frozen_attempt_policy must be the exact closed policy")
    output = _copy_json(adapter_output, "adapter_output")
    if not isinstance(output, dict):
        raise ContractError("adapter_output must be an object")
    ranking = _adapter_output_ranking(output, job)
    shown = list(job["shown_order"])
    labels = dict(job["blind_labels"])
    capture = str(frozen_attempt_policy["capture"])
    reaction_text = str(frozen_attempt_policy["reaction_text"])
    attempts: list[dict[str, object]] = []
    reactions: list[dict[str, object]] = []
    for position, creative_id in enumerate(shown, 1):
        provider_id = f"{job['dispatch_id']}-reaction-{position}"
        attempts.append(
            {
                "attempt_id": f"{provider_id}-attempt-1",
                "stage": "reaction",
                "position_seen": position,
                "attempt_number": 1,
                "provider_return_id": provider_id,
                "outcome": "accepted",
                "validation_errors": [],
            }
        )
        rank = ranking.index(creative_id) + 1
        reactions.append(
            {
                "reaction_id": f"{job['dispatch_id']}-reaction-record-{position}",
                "variation_id": creative_id,
                "display_label_seen": labels[creative_id],
                "position_seen": position,
                "reaction_label": "immediate",
                "immediate_reaction": (
                    f"{reaction_text} Frozen rank {rank} of {len(ranking)}."
                ),
                "noticed_or_understood_first": (
                    f"Frozen creative {labels[creative_id]} representation."
                ),
                "strongest_positive_signal": (
                    f"Deterministic ranking position {rank}."
                ),
                "strongest_negative_signal": (
                    "This is not a human judgment or campaign outcome."
                ),
                "judgment_status": "judged",
                "source_provenance": {
                    "provider_return_id": provider_id,
                    "capture": capture,
                },
            }
        )
    comparison_provider = f"{job['dispatch_id']}-comparison"
    attempts.append(
        {
            "attempt_id": f"{comparison_provider}-attempt-1",
            "stage": "comparison",
            "attempt_number": 1,
            "provider_return_id": comparison_provider,
            "outcome": "accepted",
            "validation_errors": [],
        }
    )
    response: dict[str, object] = {
        "study_id": job["study_id"],
        "response_id": job["response_id"],
        "record_type": job["record_type"],
        "method": job["method"],
        "synthetic_replicate_id": job["synthetic_replicate_id"],
        "reviewer_dispatch_id": job["dispatch_id"],
        "persona_archetype_id": job["persona_archetype_id"],
        "segment_id": job["segment_id"],
        "profile_snapshot": deepcopy(job["profile_snapshot"]),
        "context_attribute_provenance": deepcopy(
            job["context_attribute_provenance"]
        ),
        "worker_context_isolation": job["worker_context_isolation"],
        "human_sample_independence": False,
        "assigned_variation_ids": list(job["variation_ids"]),
        "blind_labels": labels,
        "shown_order": shown,
        "reaction_protocol": job["reaction_protocol"],
        "runtime_attempts": attempts,
        "validation": {
            "schema_valid": True,
            "assignment_valid": True,
            "reaction_order_valid": True,
        },
    }
    for field in (
        "context_stratum_id",
        "audience_slot_id",
        "grounded_profile_id",
        "profile_snapshot_sha256",
    ):
        if field in job:
            response[field] = deepcopy(job[field])
    provenance = {
        "provider_return_id": comparison_provider,
        "capture": capture,
    }
    reaction_ids = [row["reaction_id"] for row in reactions]
    record_type = job["record_type"]
    method = job["method"]
    if record_type == "screening_response" and method == "complete_exposure":
        response["per_creative_reactions"] = reactions
        response["complete_set_evaluation"] = {
            "status": "ranked",
            "preference_ranking": ranking,
            "frozen_reaction_ids": reaction_ids,
            "source_provenance": provenance,
        }
        response["usable_complete_exposure_observation"] = True
    elif (
        record_type == "screening_response"
        and method == "partial_exposure_maxdiff"
    ):
        response["per_creative_reactions"] = reactions
        response["comparative_choice"] = {
            "status": "best_worst",
            "best_variation_id": ranking[0],
            "weakest_variation_id": ranking[-1],
            "best_reason": "Highest frozen adapter ranking.",
            "weakest_reason": "Lowest frozen adapter ranking.",
            "frozen_reaction_ids": reaction_ids,
            "source_provenance": provenance,
        }
        response["usable_maxdiff_block"] = True
    elif (
        record_type == "boundary_response"
        and method == "partial_exposure_maxdiff"
    ):
        response["per_creative_reactions"] = reactions
        first, second = shown
        if ranking.index(first) < ranking.index(second):
            status = "first_preferred"
            preferred = first
        else:
            status = "second_preferred"
            preferred = second
        response["pairwise_choice"] = {
            "status": status,
            "preferred_variation_id": preferred,
            "reason": "Relative order in the frozen adapter ranking.",
            "frozen_reaction_ids": reaction_ids,
            "source_provenance": provenance,
        }
        response["usable_pairwise_observation"] = True
    elif record_type == "finalist_response":
        finalist_reviews = []
        for reaction in reactions:
            rank = ranking.index(str(reaction["variation_id"])) + 1
            score = max(1, min(5, 6 - rank))
            finalist_reviews.append(
                {
                    **reaction,
                    "rubric_scores": {
                        key: score for key in _FINALIST_RUBRIC_KEYS
                    },
                    "feedback": [
                        "Deterministic synthetic ranking projection only."
                    ],
                    "rubric_source_provenance": provenance,
                }
            )
        response["finalist_reviews"] = finalist_reviews
        response["final_preference_ranking"] = ranking
    else:
        raise ContractError("unsupported Ad Testing response projection path")
    errors = validate_response(response, job)
    if errors:
        raise ContractError(
            "projected Ad Testing response is invalid: " + "; ".join(errors)
        )
    return response


def _creative_features(
    registry: Mapping[str, object],
    creative_ids: Sequence[str],
) -> list[dict[str, object]]:
    definitions = {
        str(row["attribute_id"]): row
        for row in registry["attribute_definitions"]
    }
    rows_by_creative: dict[str, list[Mapping[str, object]]] = defaultdict(list)
    for row in registry["creative_attributes"]:
        if row["review_status"] == "approved":
            rows_by_creative[str(row["creative_id"])].append(row)
    result: list[dict[str, object]] = []
    for creative_id in creative_ids:
        attributes: set[str] = set()
        for row in rows_by_creative[creative_id]:
            definition = definitions[str(row["attribute_id"])]
            value = row["value"]
            if value is True:
                attributes.add(str(row["attribute_id"]).replace("-", " "))
                hypothesis = definition.get("behavioral_hypothesis")
                if isinstance(hypothesis, Mapping):
                    proposed = hypothesis.get("proposed_value")
                    if isinstance(proposed, list):
                        attributes.update(
                            str(item) for item in proposed if isinstance(item, str)
                        )
            elif isinstance(value, str):
                attributes.add(value)
            elif isinstance(value, (int, float)) and not isinstance(value, bool):
                attributes.add(str(value))
        result.append(
            {
                "creative_id": creative_id,
                "attributes": sorted(attributes),
            }
        )
    return result


def _exercise_panel_ref(
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
    return "exercise-panel-" + sha256_json(preimage).removeprefix("sha256:")[:24]


def _panel_roster(
    panel: Mapping[str, object],
    panel_binding: Mapping[str, object],
) -> dict[str, object]:
    members = []
    seen_profiles: set[str] = set()
    seen_panelists: set[str] = set()
    exercise_ref = str(panel_binding["exercise_panel_ref"])
    for raw in panel["grounded_context_profiles"]:
        profile = dict(raw)
        profile_id = str(profile["grounded_profile_id"])
        if profile_id in seen_profiles:
            raise ContractError("panel roster has duplicate grounded profiles")
        seen_profiles.add(profile_id)
        member_preimage = {
            "exercise_panel_ref": exercise_ref,
            "grounded_profile_id": profile_id,
        }
        suffix = sha256_json(member_preimage).removeprefix("sha256:")[:20]
        panelist_id = f"sandbox-panelist-{suffix}"
        if panelist_id in seen_panelists:
            raise ContractError("panel roster has duplicate panelist identities")
        seen_panelists.add(panelist_id)
        snapshot = deepcopy(profile["profile_snapshot"])
        members.append(
            {
                "membership_id": f"sandbox-membership-{suffix}",
                "panelist_id": panelist_id,
                "grounded_profile_id": profile_id,
                "persona_archetype_id": profile["persona_archetype_id"],
                "segment_id": profile["segment_id"],
                "context_stratum_id": profile["context_stratum_id"],
                "profile_snapshot": snapshot,
                "profile_snapshot_sha256": sha256_json(snapshot),
                "context_attribute_provenance": deepcopy(
                    profile["context_attribute_provenance"]
                ),
            }
        )
    if not members:
        raise ContractError("standalone panel must produce a nonempty sandbox roster")
    members.sort(key=lambda row: row["membership_id"])
    roster = {
        "exercise_panel_ref": exercise_ref,
        "panel_id": panel_binding["panel_id"],
        "panel_version": panel_binding["panel_version"],
        "panel_kind": panel_binding["panel_kind"],
        "candidate_id": panel_binding["candidate_id"],
        "members": members,
        "roster_sha256": None,
    }
    roster["roster_sha256"] = sha256_json(roster)
    return roster


def _ad_testing_runtime() -> dict[str, object]:
    _require_runtime_dependencies()
    if str(_AD_TESTING_SCRIPTS) not in sys.path:
        sys.path.insert(0, str(_AD_TESTING_SCRIPTS))
    from audience_lab.assignments import build_assignments
    from audience_lab.complete_exposure import aggregate_complete_exposure
    from audience_lab.finalists import aggregate_finalists
    from audience_lab.maxdiff import MaxDiffConfig, fit_maxdiff
    from audience_lab.pairwise import PairwiseConfig, resolve_boundary
    from audience_lab.planning import reserve_capacity
    from audience_lab.responses import validate_job, validate_response

    return {
        "MaxDiffConfig": MaxDiffConfig,
        "PairwiseConfig": PairwiseConfig,
        "aggregate_complete_exposure": aggregate_complete_exposure,
        "aggregate_finalists": aggregate_finalists,
        "build_assignments": build_assignments,
        "fit_maxdiff": fit_maxdiff,
        "reserve_capacity": reserve_capacity,
        "resolve_boundary": resolve_boundary,
        "validate_job": validate_job,
        "validate_response": validate_response,
    }


def _panel_authority(
    base_panel: dict[str, object],
    candidate_envelopes: Sequence[Mapping[str, object]],
) -> tuple[list[dict[str, object]], dict[str, dict[str, object]]]:
    canonical_base = _canonical_panel(base_panel)
    base_hash = sha256_json(canonical_base)
    panel_bindings: list[dict[str, object]] = [
        {
            "exercise_panel_ref": _exercise_panel_ref(
                panel_id=str(canonical_base["panel_id"]),
                panel_version=str(canonical_base["version"]),
                panel_kind="base",
                candidate_id=None,
            ),
            "panel_kind": "base",
            "candidate_id": None,
            "panel_id": canonical_base["panel_id"],
            "panel_version": canonical_base["version"],
            "panel_sha256": base_hash,
            "candidate_binding_sha256": None,
            "proposal_sha256": None,
            "panel": deepcopy(canonical_base),
            "materialized_candidate": None,
            "candidate_seal": None,
        }
    ]
    panels = {panel_bindings[0]["exercise_panel_ref"]: canonical_base}
    if (
        not isinstance(candidate_envelopes, Sequence)
        or isinstance(candidate_envelopes, (str, bytes))
        or len(candidate_envelopes) < 2
    ):
        raise ContractError(
            "candidate_bindings_and_panels must be a nonempty plural envelope"
        )
    candidate_ids: set[str] = set()
    version_keys: set[tuple[str, str]] = set()
    for index, raw in enumerate(candidate_envelopes):
        if not isinstance(raw, dict):
            raise ContractError(f"candidate envelope {index} must be an object")
        sealed = authenticate_candidate_seal_envelope(deepcopy(raw))
        materialized = sealed["materialized_candidate"]
        (
            candidate_binding,
            candidate_panel,
            reconstructed_base,
            proposal,
        ) = _authenticate_materialized(deepcopy(materialized))
        if canonical_json_bytes(reconstructed_base) != canonical_json_bytes(
            canonical_base
        ):
            raise ContractError("candidate is not derived from the admitted base panel")
        candidate_id = str(candidate_binding["candidate_id"])
        panel_id = str(candidate_panel["panel_id"])
        panel_version = str(candidate_panel["version"])
        if candidate_id in candidate_ids:
            raise ContractError("candidate IDs must be unique")
        candidate_ids.add(candidate_id)
        version_key = (panel_id, panel_version)
        if version_key in version_keys:
            raise ContractError("candidate panel ID/version pairs must be unique")
        version_keys.add(version_key)
        exercise_ref = _exercise_panel_ref(
            panel_id=panel_id,
            panel_version=panel_version,
            panel_kind="candidate",
            candidate_id=candidate_id,
        )
        binding = {
            "exercise_panel_ref": exercise_ref,
            "panel_kind": "candidate",
            "candidate_id": candidate_id,
            "panel_id": panel_id,
            "panel_version": panel_version,
            "panel_sha256": sha256_json(candidate_panel),
            "candidate_binding_sha256": candidate_binding[
                "candidate_binding_sha256"
            ],
            "proposal_sha256": proposal["proposal_sha256"],
            "panel": deepcopy(candidate_panel),
            "materialized_candidate": deepcopy(materialized),
            "candidate_seal": {
                "sealed_bundle_manifest": deepcopy(
                    sealed["sealed_bundle_manifest"]
                ),
                "candidate_seal_receipt": deepcopy(
                    sealed["candidate_seal_receipt"]
                ),
            },
        }
        panel_bindings.append(binding)
        panels[exercise_ref] = candidate_panel
    if len(panels) != len(panel_bindings):
        raise ContractError("exercise panel references must be unique")
    return panel_bindings, panels


def _build_job(
    *,
    study_id: str,
    scenario_id: str,
    repetition: int,
    panel_binding: Mapping[str, object],
    member: Mapping[str, object],
    assignment: Mapping[str, object],
    phase: str = "complete-exposure",
    record_type: str = "screening_response",
    method: str = "complete_exposure",
    variation_ids: Sequence[str] | None = None,
    shown_order: Sequence[str] | None = None,
) -> dict[str, object]:
    exercise_ref = str(panel_binding["exercise_panel_ref"])
    suffix = (
        f"{scenario_id}-r{repetition}-{exercise_ref}-{phase}-"
        f"{member['membership_id']}"
    )
    assigned = list(
        variation_ids
        if variation_ids is not None
        else assignment["variation_ids"]
    )
    shown = list(
        shown_order if shown_order is not None else assignment["shown_order"]
    )
    if set(assigned) != set(shown) or len(assigned) != len(shown):
        raise ContractError("exercise phase assignment is not an exact permutation")
    job = {
        "study_id": study_id,
        "response_id": f"response-{suffix}",
        "record_type": record_type,
        "method": method,
        "synthetic_replicate_id": f"replicate-{suffix}",
        "dispatch_id": f"dispatch-{suffix}",
        "persona_archetype_id": member["persona_archetype_id"],
        "segment_id": member["segment_id"],
        "context_stratum_id": member["context_stratum_id"],
        "audience_slot_id": f"replicate-{suffix}",
        "grounded_profile_id": member["grounded_profile_id"],
        "profile_snapshot_sha256": member["profile_snapshot_sha256"],
        "profile_snapshot": deepcopy(member["profile_snapshot"]),
        "context_attribute_provenance": deepcopy(
            member["context_attribute_provenance"]
        ),
        "worker_context_isolation": "isolated",
        "human_sample_independence": False,
        "variation_ids": assigned,
        "shown_order": shown,
        "blind_labels": {
            creative_id: chr(ord("A") + index)
            for index, creative_id in enumerate(shown)
        },
        "reaction_protocol": "progressive_reveal",
        "reaction_prompts": [
            f"Review frozen blind creative {index + 1} only."
            for index in range(len(shown))
        ],
        "comparison_prompt": (
            "Rank only the frozen blind creatives after reactions are sealed."
        ),
    }
    return job


def _finalist_job(
    screening_job: Mapping[str, object],
    ranking: Sequence[str],
    finalists: Sequence[str],
) -> dict[str, object]:
    job = deepcopy(dict(screening_job))
    job["record_type"] = "finalist_response"
    job["response_id"] = str(job["response_id"]) + "-finalist"
    job["dispatch_id"] = str(job["dispatch_id"]) + "-finalist"
    job["synthetic_replicate_id"] = (
        str(job["synthetic_replicate_id"]) + "-finalist"
    )
    job["audience_slot_id"] = job["synthetic_replicate_id"]
    job["variation_ids"] = list(finalists)
    job["shown_order"] = [
        creative_id for creative_id in ranking if creative_id in finalists
    ]
    job["blind_labels"] = {
        creative_id: chr(ord("A") + index)
        for index, creative_id in enumerate(job["shown_order"])
    }
    job["reaction_prompts"] = [
        f"Review frozen finalist {index + 1} only."
        for index in range(len(finalists))
    ]
    job["comparison_prompt"] = "Rank the frozen finalist set."
    return job


def _scoring_projections(
    *,
    runtime: Mapping[str, object],
    responses: list[dict[str, object]],
    jobs: list[dict[str, object]],
    adapter_outputs: list[dict[str, object]],
    creative_ids: list[str],
    seed: int,
) -> tuple[dict[str, object], list[dict[str, object]]]:
    complete_responses = [
        row
        for row in responses
        if row["record_type"] == "screening_response"
        and row["method"] == "complete_exposure"
    ]
    maxdiff_responses = [
        row
        for row in responses
        if row["record_type"] == "screening_response"
        and row["method"] == "partial_exposure_maxdiff"
    ]
    boundary_responses = [
        row for row in responses if row["record_type"] == "boundary_response"
    ]
    finalist_responses = [
        row for row in responses if row["record_type"] == "finalist_response"
    ]
    expected_count = len(jobs) // 4
    if (
        len(jobs) % 4
        or any(
            len(rows) != expected_count
            for rows in (
                complete_responses,
                maxdiff_responses,
                boundary_responses,
                finalist_responses,
            )
        )
    ):
        raise ContractError("exercise must carry every ordinary Ad Testing path")
    segment_counts = Counter(
        str(row["segment_id"]) for row in complete_responses
    )
    total = sum(segment_counts.values())
    segment_weights = {
        segment_id: count / total
        for segment_id, count in sorted(segment_counts.items())
    }
    complete = runtime["aggregate_complete_exposure"](
        complete_responses,
        study_id=str(complete_responses[0]["study_id"]),
        creative_ids=creative_ids,
        top_k=2,
        segment_weights=segment_weights,
        seed=seed,
    )
    maxdiff_config = runtime["MaxDiffConfig"](penalty_lambda=0.1, seed=seed)
    maxdiff = runtime["fit_maxdiff"](
        maxdiff_responses,
        maxdiff_config,
        segment_weights=segment_weights,
        creative_ids=creative_ids,
    ).as_dict()
    pairwise_config = runtime["PairwiseConfig"](
        tie_parameter=0.4,
        penalty_lambda=0.1,
        bootstrap_count=20,
        seed=seed,
    )
    boundary = runtime["resolve_boundary"](
        boundary_responses,
        2,
        pairwise_config,
        candidate_ids=creative_ids,
        segment_weights=segment_weights,
        boundary_jobs_per_wave=len(boundary_responses),
        boundary_waves_max=1,
        boundary_reserved=len(boundary_responses),
        available_boundary_reserve=len(boundary_responses),
        finalist_reserved=len(finalist_responses),
    ).to_dict()
    ranking_counts: dict[str, float] = defaultdict(float)
    output_by_dispatch = {
        str(output["dispatch_id"]): output for output in adapter_outputs
    }
    for response in complete_responses:
        output = output_by_dispatch[str(response["reviewer_dispatch_id"])]
        for row in output["ranking"]:
            ranking_counts[str(row["creative_id"])] += (
                len(creative_ids) - int(row["position"])
            )
    finalists = sorted(
        creative_ids,
        key=lambda creative_id: (-ranking_counts[creative_id], creative_id),
    )[:2]
    finalist_manifest = {
        "study_id": complete_responses[0]["study_id"],
        "method": "complete_exposure",
        "requested_shortlist_size": 2,
        "outputs": {
            "creative_asset_hashes": {
                creative_id: _digest_bytes(creative_id.encode("utf-8"))
                for creative_id in creative_ids
            }
        },
    }
    screening = {
        "study_id": complete_responses[0]["study_id"],
        "method": "complete_exposure",
        "validity_status": "valid",
        "selection_status": "resolved",
        "proposed_finalist_ids": finalists,
    }
    approval = {
        "study_id": complete_responses[0]["study_id"],
        "method": "complete_exposure",
        "approved_finalist_ids": finalists,
        "roster_decision": {
            "status": "approved",
            "approved_at": "2026-07-30T00:00:00Z",
            "approved_by": "synthetic-sandbox-harness",
            "override": False,
            "changed_after_saliency_reveal": False,
        },
    }
    finalist = runtime["aggregate_finalists"](
        finalist_manifest,
        screening,
        approval,
        finalist_responses,
    )
    verbatim_projection = {
        "capture": _ATTEMPT_POLICY["capture"],
        "exact_response_sha256": [
            sha256_json(response) for response in responses
        ],
        "reaction_records": [
            deepcopy(reaction)
            for response in responses
            for reaction in response.get(
                "per_creative_reactions",
                response.get("finalist_reviews", []),
            )
        ],
    }
    scoring_inputs = {
        "complete_exposure": {
            "study_id": complete_responses[0]["study_id"],
            "creative_ids": list(creative_ids),
            "top_k": 2,
            "segment_weights": segment_weights,
            "seed": seed,
            "response_sha256s": [
                sha256_json(row) for row in complete_responses
            ],
        },
        "maxdiff": {
            "config": asdict(maxdiff_config),
            "creative_ids": list(creative_ids),
            "segment_weights": segment_weights,
            "response_sha256s": [
                sha256_json(row) for row in maxdiff_responses
            ],
        },
        "pairwise_boundary": {
            "config": asdict(pairwise_config),
            "candidate_ids": list(creative_ids),
            "target_count": 2,
            "segment_weights": segment_weights,
            "boundary_jobs_per_wave": len(boundary_responses),
            "boundary_waves_max": 1,
            "boundary_reserved": len(boundary_responses),
            "available_boundary_reserve": len(boundary_responses),
            "finalist_reserved": len(finalist_responses),
            "response_sha256s": [
                sha256_json(row) for row in boundary_responses
            ],
        },
        "finalist_aggregation": {
            "manifest": finalist_manifest,
            "screening_result": screening,
            "approval": approval,
            "response_sha256s": [
                sha256_json(row) for row in finalist_responses
            ],
        },
    }
    numerical_binding = {
        "maxdiff_input_sha256": sha256_json(scoring_inputs["maxdiff"]),
        "maxdiff_output_sha256": sha256_json(maxdiff),
        "pairwise_input_sha256": sha256_json(
            scoring_inputs["pairwise_boundary"]
        ),
        "pairwise_output_sha256": sha256_json(boundary),
        "dependency_complete_recomputation_required": True,
    }
    scoring = {
        "scoring_inputs": scoring_inputs,
        "complete_exposure": complete,
        "maxdiff": maxdiff,
        "pairwise_boundary": boundary,
        "finalist_aggregation": finalist,
        "verbatim_projection": verbatim_projection,
        "numerical_binding": numerical_binding,
        "scoring_sha256": None,
    }
    scoring["scoring_sha256"] = sha256_json(scoring)
    return scoring, finalist_responses


def build_synthetic_panel_exercise(
    *,
    study_manifest: dict[str, object],
    public_scenario_inputs: Sequence[Mapping[str, object]],
    creative_attribute_registry: dict[str, object],
    base_panel: dict[str, object],
    candidate_bindings_and_panels: Sequence[Mapping[str, object]],
    exercise_id: str,
    exercised_at: str,
) -> dict[str, object]:
    """Build the complete sealed synthetic exercise without production authority."""

    require_identifier(exercise_id, "exercise_id")
    require_timestamp(exercised_at, "exercised_at")
    input_preimage = {
        "study_manifest": study_manifest,
        "public_scenario_inputs": public_scenario_inputs,
        "creative_attribute_registry": creative_attribute_registry,
        "base_panel": base_panel,
        "candidate_bindings_and_panels": candidate_bindings_and_panels,
    }
    input_bytes = canonical_json_bytes(input_preimage)
    manifest = validate_study_manifest(study_manifest)
    registry = validate_creative_attribute_registry(
        creative_attribute_registry
    )
    scenarios = _validate_public_scenario_inputs(
        public_scenario_inputs, manifest
    )
    panel_bindings, panels = _panel_authority(
        base_panel, candidate_bindings_and_panels
    )
    rosters = [
        _panel_roster(panels[binding["exercise_panel_ref"]], binding)
        for binding in panel_bindings
    ]
    structural_signatures = {
        (
            tuple(
                sorted(
                    Counter(
                        str(member["segment_id"])
                        for member in roster["members"]
                    ).items()
                )
            ),
            len(roster["members"]),
        )
        for roster in rosters
    }
    if len(structural_signatures) != 1:
        raise ContractError(
            "base and candidates must preserve structural roster/capacity allocation"
        )
    adapter_function, adapter_binding = _adapter_callable(manifest)
    runtime = _ad_testing_runtime()
    scenario_by_id = {
        str(row["scenario_manifest"]["scenario_binding"]["scenario_id"]): row
        for row in scenarios
    }
    roster_by_ref = {
        str(row["exercise_panel_ref"]): row for row in rosters
    }
    panelist_jobs: list[dict[str, object]] = []
    run_results: list[dict[str, object]] = []
    for family in manifest["scenario_families"]:
        scenario_id = str(family["scenario_id"])
        scenario = scenario_by_id[scenario_id]
        design = scenario["experiment_design"]
        design_cells = design["analytical_cells"]
        creative_ids = sorted(
            {
                str(arm["creative_id"])
                for cell in design_cells
                for arm in cell["arms"]
            }
        )
        if len(creative_ids) != 4:
            raise ContractError("exercise requires the frozen four-creative roster")
        features = _creative_features(registry, creative_ids)
        for repetition in range(int(family["repetitions"])):
            for binding in panel_bindings:
                exercise_ref = str(binding["exercise_panel_ref"])
                roster = roster_by_ref[exercise_ref]
                members_by_segment: dict[str, list[Mapping[str, object]]] = defaultdict(list)
                for member in roster["members"]:
                    members_by_segment[str(member["segment_id"])].append(member)
                for values in members_by_segment.values():
                    values.sort(key=lambda row: row["membership_id"])
                segment_allocations = {
                    segment_id: 2 * len(members)
                    for segment_id, members in sorted(members_by_segment.items())
                }
                member_count = len(roster["members"])
                screening_planned = 2 * member_count
                boundary_reserved = member_count
                finalist_reserved = member_count
                required_total = (
                    screening_planned
                    + boundary_reserved
                    + finalist_reserved
                )
                capacity = runtime["reserve_capacity"](
                    required_total,
                    screening_planned,
                    boundary_reserved,
                    1,
                    finalist_reserved,
                )
                if (
                    capacity.required_total != required_total
                    or not capacity.ceiling_satisfied
                    or capacity.boundary_reserved != boundary_reserved
                    or capacity.finalist_reserved != finalist_reserved
                ):
                    raise ContractError("unchanged capacity reserve math drifted")
                assignment_plan = runtime["build_assignments"](
                    creative_ids,
                    segment_allocations,
                    int(family["seed"]) + repetition,
                    capacity_plan=capacity,
                )
                assignments = assignment_plan.jobs_as_dicts()
                queues = {
                    segment_id: {
                        "complete-exposure": list(members),
                        "maxdiff-screening": list(members),
                    }
                    for segment_id, members in members_by_segment.items()
                }
                jobs: list[dict[str, object]] = []
                responses: list[dict[str, object]] = []
                adapter_outputs: list[dict[str, object]] = []
                screening_rankings: dict[
                    tuple[str, str], list[str]
                ] = {}

                def execute_phase(
                    *,
                    member: Mapping[str, object],
                    assignment: Mapping[str, object],
                    phase: str,
                    record_type: str,
                    method: str,
                    variation_ids: Sequence[str] | None = None,
                    shown_order: Sequence[str] | None = None,
                ) -> None:
                    job = _build_job(
                        study_id=(
                            f"{manifest['study_id']}-{scenario_id}-"
                            f"r{repetition}-{exercise_ref}"
                        ),
                        scenario_id=scenario_id,
                        repetition=repetition,
                        panel_binding=binding,
                        member=member,
                        assignment=assignment,
                        phase=phase,
                        record_type=record_type,
                        method=method,
                        variation_ids=variation_ids,
                        shown_order=shown_order,
                    )
                    errors = runtime["validate_job"](job)
                    if errors:
                        raise ContractError(
                            "invalid sandbox Ad Testing job: "
                            + "; ".join(errors)
                        )
                    mini_job = {
                        "dispatch_id": job["dispatch_id"],
                        "experiment_design": design,
                        "study_manifest": manifest,
                    }
                    persona_snapshot = {
                        key: deepcopy(member["profile_snapshot"][key])
                        for key in _BEHAVIOR_FIELDS
                    }
                    assigned_features = [
                        row
                        for row in features
                        if row["creative_id"] in job["variation_ids"]
                    ]
                    output = adapter_function(
                        frozen_adapter_binding=manifest[
                            "synthetic_response_adapter"
                        ],
                        panelist_job=mini_job,
                        persona_snapshot=persona_snapshot,
                        creative_attributes=assigned_features,
                    )
                    response = project_adapter_output_to_ad_testing_response(
                        adapter_output=output,
                        validated_job=job,
                        frozen_attempt_policy=_ATTEMPT_POLICY,
                    )
                    jobs.append(job)
                    responses.append(response)
                    adapter_outputs.append(output)
                    panelist_jobs.append(
                        {
                            "dispatch_id": job["dispatch_id"],
                            "phase": phase,
                            "scenario_id": scenario_id,
                            "repetition": repetition,
                            "exercise_panel_ref": exercise_ref,
                            "panel_id": binding["panel_id"],
                            "panel_version": binding["panel_version"],
                            "panelist_id": member["panelist_id"],
                            "membership_id": member["membership_id"],
                            "worker_context_isolation": "isolated",
                            "job": job,
                            "job_sha256": sha256_json(job),
                        }
                    )
                    if phase in {
                        "complete-exposure",
                        "maxdiff-screening",
                    }:
                        screening_rankings[
                            (phase, str(member["membership_id"]))
                        ] = [
                            str(row["creative_id"])
                            for row in output["ranking"]
                        ]

                for assignment in assignments:
                    segment_id = str(assignment["segment_id"])
                    phase = (
                        "complete-exposure"
                        if queues[segment_id]["complete-exposure"]
                        else "maxdiff-screening"
                    )
                    member = queues[segment_id][phase].pop(0)
                    execute_phase(
                        member=member,
                        assignment=assignment,
                        phase=phase,
                        record_type="screening_response",
                        method=(
                            "complete_exposure"
                            if phase == "complete-exposure"
                            else "partial_exposure_maxdiff"
                        ),
                    )
                if any(
                    phase_queue
                    for segment_queues in queues.values()
                    for phase_queue in segment_queues.values()
                ):
                    raise ContractError(
                        "one-worker-per-screening-phase assignment is incomplete"
                    )
                ranking_counts: dict[str, int] = defaultdict(int)
                for member in roster["members"]:
                    ranking = screening_rankings[
                        ("complete-exposure", str(member["membership_id"]))
                    ]
                    for position, creative_id in enumerate(ranking):
                        ranking_counts[creative_id] += (
                            len(creative_ids) - position
                        )
                finalists = sorted(
                    creative_ids,
                    key=lambda creative_id: (
                        -ranking_counts[creative_id],
                        creative_id,
                    ),
                )[:2]
                for member in roster["members"]:
                    membership_id = str(member["membership_id"])
                    maxdiff_ranking = screening_rankings[
                        ("maxdiff-screening", membership_id)
                    ]
                    boundary_ids = maxdiff_ranking[:2]
                    reserved_assignment = {
                        "variation_ids": boundary_ids,
                        "shown_order": boundary_ids,
                    }
                    execute_phase(
                        member=member,
                        assignment=reserved_assignment,
                        phase="pairwise-boundary",
                        record_type="boundary_response",
                        method="partial_exposure_maxdiff",
                        variation_ids=boundary_ids,
                        shown_order=boundary_ids,
                    )
                    complete_ranking = screening_rankings[
                        ("complete-exposure", membership_id)
                    ]
                    finalist_order = [
                        creative_id
                        for creative_id in complete_ranking
                        if creative_id in finalists
                    ]
                    finalist_assignment = {
                        "variation_ids": finalists,
                        "shown_order": finalist_order,
                    }
                    execute_phase(
                        member=member,
                        assignment=finalist_assignment,
                        phase="finalist-verbatim",
                        record_type="finalist_response",
                        method="complete_exposure",
                        variation_ids=finalists,
                        shown_order=finalist_order,
                    )
                scoring, finalist_responses = _scoring_projections(
                    runtime=runtime,
                    responses=responses,
                    jobs=jobs,
                    adapter_outputs=adapter_outputs,
                    creative_ids=creative_ids,
                    seed=int(family["seed"]) + repetition,
                )
                result = {
                    "scenario_family_id": scenario_id,
                    "scenario_id": scenario_id,
                    "partition": family["partition"],
                    "repetition": repetition,
                    "exercise_panel_ref": exercise_ref,
                    "panel_id": binding["panel_id"],
                    "panel_version": binding["panel_version"],
                    "panel_kind": binding["panel_kind"],
                    "candidate_id": binding["candidate_id"],
                    "scenario_manifest_sha256": scenario[
                        "scenario_manifest"
                    ]["manifest_sha256"],
                    "experiment_design_sha256": design["design_sha256"],
                    "admitted_public_files_sha256": sha256_json(
                        scenario["admitted_public_files"]
                    ),
                    "assignment_plan": _exercise_assignment_projection(jobs),
                    "assignment_plan_sha256": sha256_json(
                        _exercise_assignment_projection(jobs)
                    ),
                    "capacity_plan": asdict(capacity),
                    "capacity_plan_sha256": sha256_json(asdict(capacity)),
                    "job_sha256s": [sha256_json(job) for job in jobs],
                    "adapter_outputs": adapter_outputs,
                    "adapter_output_sha256s": [
                        sha256_json(output) for output in adapter_outputs
                    ],
                    "responses": responses,
                    "response_sha256s": [
                        sha256_json(response) for response in responses
                    ],
                    "finalist_responses": finalist_responses,
                    "finalist_response_sha256s": [
                        sha256_json(response)
                        for response in finalist_responses
                    ],
                    "scoring_and_aggregation": scoring,
                    "result_sha256": None,
                }
                result["result_sha256"] = sha256_json(result)
                run_results.append(result)
    exercise = {
        "schema_version": EXERCISE_VERSION,
        "exercise_id": exercise_id,
        "exercised_at": exercised_at,
        "study_manifest_binding": {
            "study_id": manifest["study_id"],
            "study_manifest_sha256": manifest["manifest_sha256"],
        },
        "creative_attribute_registry_binding": {
            "registry_id": registry["registry_id"],
            "registry_sha256": registry["registry_sha256"],
        },
        "frozen_adapter_binding": adapter_binding,
        "public_scenario_bindings": [
            {
                "scenario_id": row["scenario_manifest"]["scenario_binding"][
                    "scenario_id"
                ],
                "partition": row["scenario_manifest"]["partition"],
                "repetitions": row["scenario_manifest"]["scenario_binding"][
                    "repetitions"
                ],
                "scenario_manifest_sha256": row["scenario_manifest"][
                    "manifest_sha256"
                ],
                "experiment_design_sha256": row["experiment_design"][
                    "design_sha256"
                ],
                "admitted_public_files_sha256": sha256_json(
                    row["admitted_public_files"]
                ),
            }
            for row in scenarios
        ],
        "panel_bindings": panel_bindings,
        "panel_rosters": rosters,
        "panelist_jobs": panelist_jobs,
        "run_results": run_results,
        "production_authority": {
            "package_created": False,
            "resolution_created": False,
            "registration_permitted": False,
            "activation_permitted": False,
            "active_panel_mutation_permitted": False,
        },
        "limitations": [
            "Only fictional synthetic fixtures were used.",
            "The frozen adapter is a deterministic machinery probe, not a human panelist.",
            "No real-world validation, calibration, improvement, registration, or activation is established.",
        ],
        "exercise_sha256": None,
    }
    exercise["exercise_sha256"] = sha256_json(exercise)
    if canonical_json_bytes(input_preimage) != input_bytes:
        raise ContractError("exercise inputs changed during construction")
    return validate_synthetic_exercise(exercise)


__all__ = [
    "ExerciseDependencyUnavailable",
    "ExerciseSourceIsolationFailure",
    "authenticate_candidate_seal_envelope",
    "authenticate_frozen_adapter_source",
    "build_synthetic_panel_exercise",
    "load_public_scenario_inputs",
    "project_adapter_output_to_ad_testing_response",
    "runtime_dependencies_available",
]
