"""Authenticated bindings between frozen producer orderings and outcome arms.

The claim-issuing adapter in this module never accepts ordering authority from
the caller.  It authenticates the durable producer receipt and snapshot,
loads the exact frozen result from that snapshot, and only then derives the
mechanical ordering used by a held-out comparison.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
import hashlib
import json
import math
import os
import stat
import sys
from itertools import combinations
from pathlib import Path
from typing import Mapping, Sequence

from ...common import ContractError, canonical_json_bytes, sha256_json
from .contracts import (
    COMPARISON_VERSION,
    project_synthetic_result_binding,
    validate_preregistration,
    validate_validation_observation,
)
from .evidence_bindings import LINEAGE_ORDER, lineage_bundle_sha256
from .evidence_snapshot import open_evidence_snapshot
from .metrics import classify_observed_pair, normalize_observation
from .producer_evidence import validate_synthetic_producer_evidence


SYNTHETIC_SURFACES = frozenset({
    "complete_exposure_ordering",
    "maxdiff_screening_ordering",
    "pairwise_boundary_ordering",
})


@dataclass(frozen=True)
class _JsonLimits:
    maximum_depth: int = 64
    maximum_nodes: int = 1_000_000
    maximum_container_items: int = 1_000_000
    maximum_object_keys: int = 128
    maximum_string_bytes: int = 16 * 1024 * 1024


@dataclass
class _JsonCounters:
    nodes: int = 0
    container_items: int = 0
    maximum_depth_seen: int = 0


_JSON_LIMITS = _JsonLimits()


@dataclass(frozen=True)
class FrozenOrdering:
    surface: str
    run_id: str
    result_sha256: str
    ordered_groups: tuple[tuple[str, ...], ...]
    creative_hashes: tuple[tuple[str, str], ...]


def _mapping(value: object, path: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise ContractError(f"{path} must be an object")
    return value


def _nonempty_string(value: object, path: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ContractError(f"{path} must be a non-empty string")
    return value


def _digest(value: object, path: str) -> str:
    result = _nonempty_string(value, path)
    if (
        not result.startswith("sha256:")
        or len(result) != 71
        or any(char not in "0123456789abcdef" for char in result[7:])
    ):
        raise ContractError(f"{path} must be a prefixed SHA-256")
    return result


def _exact_keys(
    value: object, expected: set[str], path: str,
) -> Mapping[str, object]:
    item = _mapping(value, path)
    actual = set(item)
    if actual != expected:
        unknown = sorted(actual - expected)
        missing = sorted(expected - actual)
        detail = (
            "unknown " + ", ".join(unknown)
            if unknown
            else "missing " + ", ".join(missing)
        )
        raise ContractError(f"{path} fields are invalid ({detail})")
    return item


def _id_list(value: object, path: str) -> tuple[str, ...]:
    if not isinstance(value, list) or not value:
        raise ContractError(f"{path} must be a non-empty array")
    result = tuple(
        _nonempty_string(item, f"{path}[{index}]")
        for index, item in enumerate(value)
    )
    if len(result) != len(set(result)):
        raise ContractError(f"{path} must contain unique creative IDs")
    return result


def _finite_utilities(
    value: object, ranked_ids: tuple[str, ...], path: str,
) -> dict[str, float]:
    utilities = _mapping(value, path)
    if set(utilities) != set(ranked_ids):
        raise ContractError(f"{path} must exactly cover ranked_ids")
    result: dict[str, float] = {}
    for creative_id in ranked_ids:
        utility = utilities[creative_id]
        if (
            isinstance(utility, bool)
            or not isinstance(utility, (int, float))
            or not math.isfinite(utility)
        ):
            raise ContractError(f"{path}.{creative_id} must be finite numeric")
        result[creative_id] = float(utility)
    return result


def _finite_json(
    value: object,
    path: str = "$",
    *,
    limits: _JsonLimits = _JSON_LIMITS,
    counters: _JsonCounters | None = None,
) -> _JsonCounters:
    """Validate finite acyclic JSON under closed, test-injectable limits."""
    if not isinstance(limits, _JsonLimits):
        raise ContractError(f"{path} JSON limits are invalid")
    observed = counters if counters is not None else _JsonCounters()
    if not isinstance(observed, _JsonCounters):
        raise ContractError(f"{path} JSON counters are invalid")
    active_containers: set[int] = set()
    # Frames are (exiting, value, diagnostic path, container depth).
    stack: list[tuple[bool, object, str, int]] = [
        (False, value, path, 1)
    ]
    try:
        while stack:
            exiting, item, item_path, depth = stack.pop()
            if exiting:
                active_containers.remove(id(item))
                continue

            observed.nodes += 1
            if observed.nodes > limits.maximum_nodes:
                raise ContractError(
                    f"{path} exceeds JSON node limit"
                )
            if item is None or type(item) in {bool, int}:  # noqa: E721
                continue
            if type(item) is float:  # noqa: E721
                if not math.isfinite(item):
                    raise ContractError(f"{item_path} must be finite JSON")
                continue
            if type(item) is str:  # noqa: E721
                if len(item.encode("utf-8")) > limits.maximum_string_bytes:
                    raise ContractError(
                        f"{item_path} exceeds JSON string byte limit"
                    )
                continue
            if type(item) not in {list, dict}:  # noqa: E721
                raise ContractError(
                    f"{item_path} must use only exact JSON-compatible types"
                )

            if depth > limits.maximum_depth:
                raise ContractError(f"{path} exceeds JSON depth limit")
            observed.maximum_depth_seen = max(
                observed.maximum_depth_seen, depth,
            )
            identity = id(item)
            if identity in active_containers:
                raise ContractError(f"{item_path} contains a JSON cycle")
            active_containers.add(identity)
            stack.append((True, item, item_path, depth))

            item_count = len(item)
            observed.container_items += item_count
            if observed.container_items > limits.maximum_container_items:
                raise ContractError(
                    f"{path} exceeds JSON container-item limit"
                )
            if type(item) is dict:  # noqa: E721
                if item_count > limits.maximum_object_keys:
                    raise ContractError(
                        f"{item_path} exceeds JSON object-key limit"
                    )
                children: list[tuple[object, str]] = []
                for index, (key, child) in enumerate(item.items()):
                    if type(key) is not str:  # noqa: E721
                        raise ContractError(
                            f"{item_path} JSON object keys must be strings"
                        )
                    if (
                        len(key.encode("utf-8"))
                        > limits.maximum_string_bytes
                    ):
                        raise ContractError(
                            f"{item_path}.key[{index}] exceeds JSON string byte limit"
                        )
                    children.append(
                        (child, f"{item_path}.value[{index}]")
                    )
            else:
                children = [
                    (child, f"{item_path}[{index}]")
                    for index, child in enumerate(item)
                ]
            for child, child_path in reversed(children):
                stack.append((False, child, child_path, depth + 1))
    except ContractError:
        raise
    except (
        MemoryError, RecursionError, OverflowError, UnicodeError,
        TypeError, ValueError, KeyError,
    ) as exc:
        raise ContractError(
            f"{path} cannot be validated as bounded JSON"
        ) from exc
    return observed


def _canonical_identity(value: object, *, path: str) -> bytes:
    _finite_json(value, path)
    try:
        return canonical_json_bytes(value)
    except (
        TypeError, ValueError, OverflowError, RecursionError, MemoryError,
        UnicodeError,
    ) as exc:
        raise ContractError(
            f"{path} cannot be represented as closed canonical JSON"
        ) from exc


_AUDIENCE_KEYS = {"audience_package", "audience_lock"}
_COMPLETE_KEYS = {
    "study_id", "method", "estimand", "stability_diagnostic",
    "requested_top_k", "utilities", "ranked_ids",
    "top_k_inclusion_frequencies", "classifications", "selection_status",
    "proposed_finalist_ids", "archetype_sensitivity", "model_diagnostics",
    "recovery_config_version", "validity_status", "validity_reasons",
    "interpretation_limits",
}
_MAXDIFF_KEYS = {
    "study_id", "method", "estimand", "stability_diagnostic",
    "requested_top_k", "utilities", "ranked_ids",
    "top_k_inclusion_frequencies", "classifications",
    "selection_status", "proposed_finalist_ids",
    "archetype_sensitivity", "model_diagnostics",
    "recovery_config_version", "validity_status", "validity_reasons",
    "interpretation_limits",
}
_PAIRWISE_KEYS = {
    "study_id", "status", "status_reasons", "estimand",
    "stability_diagnostic", "boundary_candidate_ids",
    "frozen_clear_finalist_ids", "frozen_clear_non_finalist_ids",
    "selected_boundary_ids", "proposed_finalist_ids", "utilities",
    "ranked_ids", "conditional_inclusion_frequencies", "classifications",
    "model_diagnostics", "decision_audit", "interpretation_limits",
}


def _result_document(
    result: Mapping[str, object], *, surface: str,
) -> Mapping[str, object]:
    if surface == "complete_exposure_ordering":
        base = _COMPLETE_KEYS
    elif surface == "maxdiff_screening_ordering":
        status = result.get("selection_status")
        if status == "resolved":
            base = _MAXDIFF_KEYS
        elif status == "boundary_required":
            base = _MAXDIFF_KEYS | {"boundary_plan"}
        else:
            raise ContractError(
                "frozen MaxDiff result selection_status is invalid"
            )
    else:
        base = _PAIRWISE_KEYS
    keys = set(result)
    if keys == base:
        return result
    if keys == base | _AUDIENCE_KEYS:
        return result
    if bool(keys & _AUDIENCE_KEYS):
        raise ContractError(
            "result audience_package and audience_lock must appear together"
        )
    return _exact_keys(result, base, "frozen result")


_MAX_SNAPSHOT_MEMBER_BYTES = 256 * 1024 * 1024
_DIR_FLAGS = (
    os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_NOFOLLOW", 0)
    | getattr(os, "O_CLOEXEC", 0)
)
_READ_FLAGS = (
    os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    | getattr(os, "O_CLOEXEC", 0)
)


def _identity(value: os.stat_result) -> tuple[int, int]:
    return value.st_dev, value.st_ino


def _stat_key(value: os.stat_result) -> tuple[int, ...]:
    return (
        value.st_dev, value.st_ino, value.st_mode, value.st_uid,
        value.st_gid, value.st_nlink, value.st_size,
        value.st_mtime_ns, value.st_ctime_ns,
    )


def _read_snapshot_bytes(path: Path, *, label: str) -> bytes:
    if not isinstance(path, Path) or not path.is_absolute():
        raise ContractError(f"{label} path must be absolute")
    # Darwin exposes these two system aliases as symlinks.  Canonicalize only
    # the fixed OS aliases on Darwin; never resolve a caller-controlled path.
    if sys.platform == "darwin":
        for alias, canonical in (
            (Path("/var"), Path("/private/var")),
            (Path("/tmp"), Path("/private/tmp")),
        ):
            try:
                relative = path.relative_to(alias)
            except ValueError:
                continue
            path = canonical / relative
            break
    components = path.parts[1:]
    if not components or any(item in {"", ".", ".."} for item in components):
        raise ContractError(f"{label} path is unsafe")
    directory_fds: list[tuple[int, tuple[int, int]]] = []
    file_fd: int | None = None
    try:
        current = os.open("/", _DIR_FLAGS)
        directory_fds.append((current, _identity(os.fstat(current))))
        for component in components[:-1]:
            child = os.open(component, _DIR_FLAGS, dir_fd=current)
            value = os.fstat(child)
            entry = os.stat(component, dir_fd=current, follow_symlinks=False)
            if (
                not stat.S_ISDIR(value.st_mode)
                or _identity(value) != _identity(entry)
            ):
                raise ContractError(f"{label} parent chain changed")
            directory_fds.append((child, _identity(value)))
            current = child
        file_fd = os.open(components[-1], _READ_FLAGS, dir_fd=current)
        before = os.fstat(file_fd)
        entry = os.stat(
            components[-1], dir_fd=current, follow_symlinks=False,
        )
        if (
            not stat.S_ISREG(before.st_mode)
            or _identity(before) != _identity(entry)
            or before.st_uid != os.geteuid()
            or before.st_nlink != 1
            or stat.S_IMODE(before.st_mode) != 0o400
            or before.st_size > _MAX_SNAPSHOT_MEMBER_BYTES
        ):
            raise ContractError(f"{label} member identity or metadata is unsafe")
        chunks: list[bytes] = []
        total = 0
        while total <= _MAX_SNAPSHOT_MEMBER_BYTES:
            chunk = os.read(
                file_fd,
                min(
                    1024 * 1024,
                    _MAX_SNAPSHOT_MEMBER_BYTES + 1 - total,
                ),
            )
            if not chunk:
                break
            chunks.append(chunk)
            total += len(chunk)
        after = os.fstat(file_fd)
        if (
            total > _MAX_SNAPSHOT_MEMBER_BYTES
            or total != before.st_size
            or _stat_key(before) != _stat_key(after)
            or _identity(after) != _identity(os.stat(
                components[-1], dir_fd=current, follow_symlinks=False,
            ))
        ):
            raise ContractError(f"{label} changed while read")
        for index in range(1, len(directory_fds)):
            parent_fd = directory_fds[index - 1][0]
            child_fd, expected = directory_fds[index]
            child_value = os.fstat(child_fd)
            entry_value = os.stat(
                components[index - 1],
                dir_fd=parent_fd,
                follow_symlinks=False,
            )
            if (
                _identity(child_value) != expected
                or _identity(entry_value) != expected
            ):
                raise ContractError(f"{label} parent chain changed")
        return b"".join(chunks)
    except (OSError, MemoryError, OverflowError) as exc:
        raise ContractError(f"{label} could not be read safely") from exc
    finally:
        if file_fd is not None:
            os.close(file_fd)
        for directory_fd, _expected in reversed(directory_fds):
            os.close(directory_fd)


def _read_json(path: Path, *, label: str) -> tuple[dict[str, object], bytes]:
    def no_duplicates(pairs: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f"duplicate key {key}")
            result[key] = value
        return result

    try:
        raw = _read_snapshot_bytes(path, label=label)
        value = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=no_duplicates,
            parse_constant=lambda token: (_ for _ in ()).throw(
                ValueError(f"non-finite constant {token}")
            ),
        )
    except (
        UnicodeError, json.JSONDecodeError, ValueError, RecursionError,
    ) as exc:
        raise ContractError(f"{label} is not one finite UTF-8 JSON document") from exc
    if not isinstance(value, dict):
        raise ContractError(f"{label} must be a JSON object")
    return value, raw


def _creative_hashes_from_manifest(
    manifest: Mapping[str, object],
) -> tuple[tuple[str, str], ...]:
    outputs = _mapping(manifest.get("outputs"), "study-manifest.json.outputs")
    hashes = _mapping(
        outputs.get("creative_asset_hashes"),
        "study-manifest.json.outputs.creative_asset_hashes",
    )
    if not hashes:
        raise ContractError(
            "study-manifest.json.outputs.creative_asset_hashes must not be empty"
        )
    result = tuple(sorted(
        (
            _nonempty_string(creative_id, "creative ID"),
            _digest(content_hash, f"creative hash for {creative_id}"),
        )
        for creative_id, content_hash in hashes.items()
    ))
    if len(result) != len({creative_id for creative_id, _ in result}):
        raise ContractError("study manifest creative IDs must be unique")
    return result


def _tie_policy(
    *, surface: str, evidence: Mapping[str, object],
) -> dict[str, object]:
    semantics = _mapping(
        evidence.get("producer_semantics"), "producer evidence.producer_semantics",
    )
    policy = _mapping(
        semantics.get("policy_bindings"),
        "producer evidence.producer_semantics.policy_bindings",
    )
    if surface == "complete_exposure_ordering":
        result = {
            "ordering_equivalence": policy.get("ordering_equivalence"),
            "ordering_tiebreak": policy.get("ordering_tiebreak"),
        }
        fixed = {
            "ordering_equivalence": "exact-utility-equality-v1",
            "ordering_tiebreak": "creative-id-serialization-only-v1",
        }
    else:
        result = {
            "ordering_equivalence": policy.get("ordering_equivalence"),
            "ordering_tiebreak": policy.get("ordering_tiebreak"),
            "effective_ordering_tolerance": policy.get(
                "effective_ordering_tolerance"
            ),
            "rounding_rule": policy.get("rounding_rule"),
        }
        fixed = {
            "ordering_equivalence": "rounded-utility-bucket-v1",
            "ordering_tiebreak": "creative-id-serialization-only-v1",
            "rounding_rule": "python-half-even-v1",
        }
        tolerance = result["effective_ordering_tolerance"]
        if (
            isinstance(tolerance, bool)
            or not isinstance(tolerance, (int, float))
            or not math.isfinite(tolerance)
            or tolerance <= 0
        ):
            raise ContractError(
                "authenticated tie-policy tolerance must be finite and positive"
            )
    if any(result.get(name) != expected for name, expected in fixed.items()):
        raise ContractError(
            "authenticated producer tie-policy projection is invalid"
        )
    _canonical_identity(result, path="authenticated tie-policy projection")
    return result


def _ordered_groups(
    *,
    surface: str,
    ranked_ids: tuple[str, ...],
    utilities: Mapping[str, float],
    policy: Mapping[str, object],
) -> tuple[tuple[str, ...], ...]:
    if surface == "complete_exposure_ordering":
        keys: tuple[object, ...] = tuple(utilities[item] for item in ranked_ids)
    else:
        tolerance = policy.get("effective_ordering_tolerance")
        if (
            isinstance(tolerance, bool)
            or not isinstance(tolerance, (int, float))
            or not math.isfinite(tolerance)
            or tolerance <= 0
        ):
            raise ContractError(
                "authenticated effective ordering tolerance must be finite and positive"
            )
        bucket_keys: list[int] = []
        for item in ranked_ids:
            try:
                quotient = utilities[item] / float(tolerance)
            except (ArithmeticError, OverflowError, ValueError, TypeError) as exc:
                raise ContractError(
                    "authenticated ordering bucket arithmetic is invalid"
                ) from exc
            if not math.isfinite(quotient):
                raise ContractError(
                    "authenticated ordering bucket quotient must be finite"
                )
            try:
                bucket_keys.append(round(quotient))
            except (ArithmeticError, OverflowError, ValueError, TypeError) as exc:
                raise ContractError(
                    "authenticated ordering bucket rounding failed"
                ) from exc
        keys = tuple(bucket_keys)

    groups: list[tuple[str, ...]] = []
    current: list[str] = []
    previous: object | None = None
    for creative_id, comparator in zip(ranked_ids, keys):
        if current and comparator != previous:
            groups.append(tuple(current))
            current = []
        current.append(creative_id)
        previous = comparator
    if current:
        groups.append(tuple(current))

    # Producer rankings are descending.  Grouping may remove directions, but
    # it may never repair, reorder, or conceal a comparator-inconsistent list.
    if any(
        isinstance(keys[index - 1], (int, float))
        and isinstance(keys[index], (int, float))
        and keys[index - 1] < keys[index]
        for index in range(1, len(keys))
    ):
        raise ContractError(
            "ranked_ids are inconsistent with the authenticated comparator"
        )
    return tuple(groups)


def _validate_receipt_projection(
    *,
    registered: Mapping[str, object],
    evidence: Mapping[str, object],
    manifest: Mapping[str, object],
    creative_hashes: tuple[tuple[str, str], ...],
) -> None:
    surface = _mapping(
        registered.get("synthetic_surface"),
        "preregistration.synthetic_surface",
    )
    inputs = _mapping(evidence.get("input_bindings"), "producer evidence.input_bindings")
    result_binding = _mapping(
        evidence.get("result_binding"), "producer evidence.result_binding",
    )
    semantics = _mapping(
        evidence.get("producer_semantics"), "producer evidence.producer_semantics",
    )
    expected = {
        "surface": evidence.get("surface"),
        "method": evidence.get("method"),
        "stage": evidence.get("stage"),
        "run_id": evidence.get("run_id"),
        "result_path": result_binding.get("path"),
        "result_sha256": result_binding.get("canonical_document_sha256"),
        "result_bytes_sha256": result_binding.get("raw_bytes_sha256"),
        "manifest_sha256": _mapping(
            inputs.get("study_manifest"), "producer evidence.study_manifest",
        ).get("canonical_document_sha256"),
        "lineage_bundle_sha256": lineage_bundle_sha256({
            role: dict(_mapping(inputs.get(role), f"producer evidence.{role}"))
            for role in LINEAGE_ORDER
        }),
        "producer_evidence_sha256": evidence.get("producer_evidence_sha256"),
        "producer_semantics_sha256": semantics.get(
            "producer_semantics_sha256"
        ),
        "frozen_at": evidence.get("frozen_at"),
        "producer_evidence_sealed_at": evidence.get("sealed_at"),
        "eligible_creatives": [
            {"creative_id": creative_id, "creative_sha256": creative_sha256}
            for creative_id, creative_sha256 in creative_hashes
        ],
    }
    if dict(surface) != expected:
        raise ContractError(
            "sealed synthetic surface does not equal authenticated producer evidence"
        )
    if manifest.get("study_id") != evidence.get("run_id"):
        raise ContractError(
            "authenticated manifest study_id must equal the producer run_id"
        )


def _load_result_ordering(
    *,
    surface: str,
    result: Mapping[str, object],
    run_id: str,
    creative_hashes: tuple[tuple[str, str], ...],
    policy: Mapping[str, object],
) -> tuple[tuple[str, ...], ...]:
    document = _result_document(result, surface=surface)
    if document.get("study_id") != run_id:
        raise ContractError("frozen result study_id must match the producer run_id")
    if surface == "complete_exposure_ordering":
        if (
            document.get("method") != "complete_exposure"
            or document.get("validity_status") != "valid"
        ):
            raise ContractError(
                "complete exposure result must be a valid complete_exposure result"
            )
    elif surface == "maxdiff_screening_ordering":
        if (
            document.get("method") != "partial_exposure_maxdiff"
            or document.get("validity_status") != "valid"
        ):
            raise ContractError(
                "MaxDiff result must be a valid partial_exposure_maxdiff result"
            )
    elif document.get("status") != "resolved":
        raise ContractError("pairwise boundary result must be resolved")

    ranked_ids = _id_list(document.get("ranked_ids"), "frozen result.ranked_ids")
    roster = {creative_id for creative_id, _ in creative_hashes}
    if surface == "pairwise_boundary_ordering":
        candidates = _id_list(
            document.get("boundary_candidate_ids"),
            "frozen result.boundary_candidate_ids",
        )
        if set(ranked_ids) != set(candidates):
            raise ContractError(
                "pairwise ranked_ids must exactly cover boundary_candidate_ids"
            )
        if not set(ranked_ids).issubset(roster):
            raise ContractError(
                "pairwise boundary candidates must belong to the full eligible roster"
            )
    elif set(ranked_ids) != roster:
        raise ContractError(
            "complete and MaxDiff ranked_ids must cover the full eligible roster"
        )
    utilities = _finite_utilities(
        document.get("utilities"), ranked_ids, "frozen result.utilities",
    )
    return _ordered_groups(
        surface=surface,
        ranked_ids=ranked_ids,
        utilities=utilities,
        policy=policy,
    )


def load_frozen_ordering(
    *,
    surface: str,
    result: dict[str, object],
    registration: dict[str, object],
    evidence_root: Path,
    snapshot_root: Path,
) -> FrozenOrdering:
    """Authenticate durable producer evidence and load its exact result."""
    registered = validate_preregistration(registration)
    if surface not in SYNTHETIC_SURFACES:
        raise ContractError("synthetic surface is unsupported")
    sealed = _mapping(
        registered["synthetic_surface"], "preregistration.synthetic_surface",
    )
    if sealed["surface"] != surface:
        raise ContractError("synthetic surface must match the preregistration")
    run_id = _nonempty_string(sealed["run_id"], "synthetic surface run_id")
    result_sha256 = _digest(
        sealed["result_sha256"], "synthetic surface result_sha256",
    )
    evidence = validate_synthetic_producer_evidence(
        surface=surface,
        run_id=run_id,
        result_sha256=result_sha256,
        evidence_root=evidence_root,
        snapshot_root=snapshot_root,
    )
    with open_evidence_snapshot(
        surface=surface,
        run_id=run_id,
        result_sha256=result_sha256,
        snapshot_root=snapshot_root,
    ) as snapshot:
        snapshot_binding = _mapping(
            evidence.get("snapshot_binding"), "producer evidence.snapshot_binding",
        )
        if (
            snapshot.snapshot_id != snapshot_binding.get("snapshot_id")
            or snapshot.snapshot_sha256 != snapshot_binding.get("snapshot_sha256")
            or snapshot.archive_sha256 != snapshot_binding.get("archive_sha256")
            or snapshot.frozen_at != evidence.get("frozen_at")
        ):
            raise ContractError(
                "authenticated snapshot does not match the producer receipt"
            )
        manifest, manifest_raw = _read_json(
            snapshot.resolve_member("study_manifest"),
            label="authenticated study manifest",
        )
        snapshot_result, result_raw = _read_json(
            snapshot.resolve_member("result"),
            label="authenticated frozen result",
        )

    result_binding = _mapping(
        evidence.get("result_binding"), "producer evidence.result_binding",
    )
    if (
        sha256_json(snapshot_result)
        != result_binding.get("canonical_document_sha256")
        or "sha256:" + hashlib.sha256(result_raw).hexdigest()
        != result_binding.get("raw_bytes_sha256")
    ):
        raise ContractError(
            "authenticated result bytes do not match the producer receipt"
        )
    if _canonical_identity(
        result, path="caller frozen result",
    ) != _canonical_identity(
        snapshot_result, path="authenticated snapshot result",
    ):
        raise ContractError(
            "caller result must equal the exact authenticated snapshot result"
        )
    inputs = _mapping(evidence.get("input_bindings"), "producer evidence.input_bindings")
    manifest_binding = _mapping(
        inputs.get("study_manifest"), "producer evidence.study_manifest",
    )
    if (
        sha256_json(manifest)
        != manifest_binding.get("canonical_document_sha256")
        or "sha256:" + hashlib.sha256(manifest_raw).hexdigest()
        != manifest_binding.get("raw_bytes_sha256")
    ):
        raise ContractError(
            "authenticated manifest does not match the producer receipt"
        )
    creative_hashes = _creative_hashes_from_manifest(manifest)
    _validate_receipt_projection(
        registered=registered,
        evidence=evidence,
        manifest=manifest,
        creative_hashes=creative_hashes,
    )
    policy = _tie_policy(surface=surface, evidence=evidence)
    analysis = _mapping(
        registered["analysis_rules"], "preregistration.analysis_rules",
    )
    if _canonical_identity(
        analysis.get("tie_handling"),
        path="preregistered tie handling",
    ) != _canonical_identity(
        policy,
        path="authenticated tie handling",
    ):
        raise ContractError(
            "preregistered tie handling does not equal authenticated producer policy"
        )
    groups = _load_result_ordering(
        surface=surface,
        result=snapshot_result,
        run_id=run_id,
        creative_hashes=creative_hashes,
        policy=policy,
    )
    ordering_ids = {
        creative_id for group in groups for creative_id in group
    }
    ordering_hashes = tuple(
        (creative_id, content_hash)
        for creative_id, content_hash in creative_hashes
        if creative_id in ordering_ids
    )
    return FrozenOrdering(
        surface, run_id, result_sha256, groups, ordering_hashes,
    )


def _synthetic_direction(
    groups: tuple[tuple[str, ...], ...], left: str, right: str,
) -> str:
    positions = {
        creative_id: index
        for index, group in enumerate(groups)
        for creative_id in group
    }
    if positions[left] < positions[right]:
        return "synthetic_a_above_b"
    if positions[left] > positions[right]:
        return "synthetic_b_above_a"
    return "synthetic_tie"


def derive_pair_directions(
    ordering: FrozenOrdering,
) -> tuple[dict[str, object], ...]:
    """Mechanically project pair directions without issuing a claim document."""
    if not isinstance(ordering, FrozenOrdering):
        raise ContractError("ordering must be a FrozenOrdering")
    flat = tuple(
        creative_id
        for group in ordering.ordered_groups
        for creative_id in group
    )
    if (
        ordering.surface not in SYNTHETIC_SURFACES
        or not ordering.ordered_groups
        or any(not group for group in ordering.ordered_groups)
        or len(flat) != len(set(flat))
    ):
        raise ContractError("frozen ordering is structurally invalid")
    return tuple({
        "creative_a": left,
        "creative_b": right,
        "synthetic_direction": _synthetic_direction(
            ordering.ordered_groups, left, right,
        ),
    } for left, right in combinations(sorted(flat), 2))


def build_synthetic_outcome_comparison(
    *,
    registration: dict[str, object],
    result: dict[str, object],
    evidence_root: Path,
    snapshot_root: Path,
    observations: Sequence[dict[str, object]],
) -> dict[str, object]:
    """Build one held-out comparison from an internally authenticated result."""
    registered = validate_preregistration(registration)
    sealed = _mapping(
        registered["synthetic_surface"], "preregistration.synthetic_surface",
    )
    ordering = load_frozen_ordering(
        surface=str(sealed["surface"]),
        result=result,
        registration=registration,
        evidence_root=evidence_root,
        snapshot_root=snapshot_root,
    )
    directions = derive_pair_directions(ordering)
    flat = tuple(
        creative_id
        for group in ordering.ordered_groups
        for creative_id in group
    )
    if len(flat) < 3:
        raise ContractError(
            "C1 comparison requires at least three authenticated creative arms"
        )
    if len(observations) != len(flat):
        raise ContractError(
            "outcome observations must exactly cover the authenticated ordering"
        )

    expected_hashes = dict(ordering.creative_hashes)
    for observation in observations:
        raw = _mapping(observation, "outcome observation")
        creative = _mapping(
            raw.get("creative_binding"), "observation.creative_binding",
        )
        creative_id = _nonempty_string(
            creative.get("creative_id"), "observation creative_id",
        )
        if expected_hashes.get(creative_id) != creative.get("creative_sha256"):
            raise ContractError(
                "outcome creative hash must match the exact frozen creative input"
            )

    validated = [
        validate_validation_observation(item) for item in observations
    ]
    normalized = [normalize_observation(item) for item in validated]
    first = validated[0]
    compact_binding = project_synthetic_result_binding(sealed)
    same_fields = (
        "registration_binding", "panel_binding", "synthetic_binding",
        "claim_scope", "outcome_scope", "metric", "metric_family", "units",
        "windows", "assignment", "block_id",
    )
    for observation in validated:
        for field in same_fields:
            if _canonical_identity(
                observation[field],
                path=f"observation.{field}",
            ) != _canonical_identity(
                first[field],
                path=f"first observation.{field}",
            ):
                raise ContractError(
                    "outcome observations must share the exact registered "
                    "block, scope, metric, assignment, and bindings"
                )
        if observation["holdout_status"] != "eligible_held_out":
            raise ContractError(
                "outcome observation is not an eligible held-out arm"
            )
        observation_registration = _mapping(
            observation["registration_binding"],
            "observation.registration_binding",
        )
        if (
            observation_registration["registration_id"]
            != registered["registration_id"]
            or observation_registration["registration_sha256"]
            != registered["registration_sha256"]
            or _canonical_identity(
                observation_registration["preregistration"],
                path="observation bound preregistration",
            ) != _canonical_identity(
                registered, path="externally supplied preregistration",
            )
        ):
            raise ContractError(
                "outcome observation must bind the externally supplied "
                "sealed preregistration"
            )
        if _canonical_identity(
            observation["panel_binding"],
            path="observation panel binding",
        ) != _canonical_identity(
            registered["panel_binding"],
            path="registered panel binding",
        ):
            raise ContractError(
                "outcome observation panel/package binding must match "
                "the preregistration"
            )
        if _canonical_identity(
            observation["synthetic_binding"],
            path="observation synthetic binding",
        ) != _canonical_identity(
            compact_binding, path="registered synthetic binding",
        ):
            raise ContractError(
                "outcome observation run/result binding must match "
                "the preregistration"
            )
        if _canonical_identity(
            observation["claim_scope"], path="observation claim scope",
        ) != _canonical_identity(
            registered["claim_scope"], path="registered claim scope",
        ):
            raise ContractError(
                "outcome observation claim scope must match the preregistration"
            )
        registered_scope = _mapping(
            registered["claim_scope"], "preregistration.claim_scope",
        )
        if _canonical_identity(
            observation["outcome_scope"],
            path="observation outcome scope",
        ) != _canonical_identity(
            registered_scope["outcome_scope"],
            path="registered outcome scope",
        ):
            raise ContractError(
                "outcome observation outcome scope must match the preregistration"
            )
        if _canonical_identity(
            observation["metric"], path="observation metric",
        ) != _canonical_identity(
            registered["primary_metric"], path="registered primary metric",
        ):
            raise ContractError(
                "outcome observation metric must match the preregistration"
            )
        creative = _mapping(
            observation["creative_binding"], "observation.creative_binding",
        )
        creative_id = _nonempty_string(
            creative["creative_id"], "observation creative_id",
        )
        if expected_hashes.get(creative_id) != creative["creative_sha256"]:
            raise ContractError(
                "outcome creative hash must match the exact frozen creative input"
            )

    creative_ids = [arm.creative_id for arm in normalized]
    arm_ids = [arm.arm_id for arm in normalized]
    if (
        len(creative_ids) != len(set(creative_ids))
        or len(arm_ids) != len(set(arm_ids))
    ):
        raise ContractError(
            "outcome arms must map each creative and arm exactly once"
        )
    if set(creative_ids) != set(flat):
        raise ContractError(
            "outcome arms must provide a complete one-to-one creative mapping"
        )
    block = next((
        item
        for item in registered["validation_blocks"]
        if item["block_id"] == first["block_id"]
    ), None)
    if (
        not isinstance(block, Mapping)
        or set(block["planned_arm_ids"]) != set(arm_ids)
    ):
        raise ContractError(
            "outcome arms must exactly match the preregistered block plan"
        )
    for observation in validated:
        shared = _mapping(
            observation["shared_outcome_evidence_binding"],
            "observation.shared_outcome_evidence_binding",
        )
        if shared["study_id"] != block["study_id"]:
            raise ContractError(
                "outcome observation study must match the preregistered block"
            )

    arms_by_creative = {arm.creative_id: arm for arm in normalized}
    validated_by_creative = {
        arm.creative_id: document
        for arm, document in zip(normalized, validated)
    }
    mappings = [{
        "arm_id": arms_by_creative[creative_id].arm_id,
        "creative_binding": validated_by_creative[creative_id][
            "creative_binding"
        ],
        "observation_sha256": validated_by_creative[creative_id][
            "observation_sha256"
        ],
    } for creative_id in sorted(arms_by_creative)]
    observed_groups = tuple(
        tuple(sorted(
            arm.creative_id
            for arm in normalized
            if arm.direction_normalized_point == value
        ))
        for value in sorted({
            arm.direction_normalized_point for arm in normalized
        }, reverse=True)
    )
    margin = _mapping(
        registered["primary_metric"], "preregistration.primary_metric",
    )["practical_equivalence_margin"]
    observed_by_pair: dict[tuple[str, str], str] = {}
    for left, right in combinations(sorted(arms_by_creative), 2):
        observed, _interval = classify_observed_pair(
            arms_by_creative[left],
            arms_by_creative[right],
            equivalence_margin=float(margin),
        )
        observed_by_pair[(left, right)] = observed
    pairs = [{
        **row,
        "observed_direction": observed_by_pair[
            (str(row["creative_a"]), str(row["creative_b"]))
        ],
    } for row in directions]
    observations_by_arm = {
        str(observation["arm_id"]): observation for observation in validated
    }
    ordered_observations = [
        observations_by_arm[arm_id] for arm_id in sorted(observations_by_arm)
    ]
    segment_ids = sorted({
        str(segment_id)
        for observation in ordered_observations
        for segment_id in observation["segment_ids"]
    })

    def _filtered_groups(
        groups: Sequence[Sequence[str]], selected: set[str],
    ) -> list[list[str]]:
        return [
            [creative_id for creative_id in group if creative_id in selected]
            for group in groups
            if any(creative_id in selected for creative_id in group)
        ]

    segment_evidence: list[dict[str, object]] = []
    for segment_id in segment_ids:
        segment_observations = [
            observation for observation in ordered_observations
            if segment_id in observation["segment_ids"]
        ]
        selected = {
            str(observation["creative_binding"]["creative_id"])
            for observation in segment_observations
        }
        segment_evidence.append({
            "segment_id": segment_id,
            "observation_sha256": [
                observation["observation_sha256"]
                for observation in segment_observations
            ],
            "arm_ids": [
                observation["arm_id"] for observation in segment_observations
            ],
            "observed_ordering": _filtered_groups(
                observed_groups, selected,
            ),
            "synthetic_ordering": _filtered_groups(
                ordering.ordered_groups, selected,
            ),
            "pairwise_comparisons": [
                pair for pair in pairs
                if pair["creative_a"] in selected
                and pair["creative_b"] in selected
            ],
        })
    document: dict[str, object] = {
        "schema_version": COMPARISON_VERSION,
        "comparison_id": (
            f"{registered['registration_id']}-{first['block_id']}"
        ),
        "registration_binding": {
            "registration_id": registered["registration_id"],
            "registration_sha256": registered["registration_sha256"],
        },
        "panel_binding": registered["panel_binding"],
        "synthetic_result_binding": compact_binding,
        "block_binding": {
            "block_id": first["block_id"],
            "study_id": block["study_id"],
        },
        "metric_binding": registered["primary_metric"],
        "observations": deepcopy(ordered_observations),
        "arm_mappings": mappings,
        "mapping_coverage": {
            "expected_arms": len(flat), "mapped_arms": len(mappings),
        },
        "observed_ordering": [list(group) for group in observed_groups],
        "synthetic_ordering": [
            list(group) for group in ordering.ordered_groups
        ],
        "pairwise_comparisons": pairs,
        "block_evidence": {
            "observation_sha256": [
                observation["observation_sha256"]
                for observation in ordered_observations
            ],
            "eligible_exposure_count": sum(
                int(observation["missingness"]["eligible_exposure_count"])
                for observation in ordered_observations
            ),
            "missing_outcome_count": sum(
                int(observation["missingness"]["missing_outcome_count"])
                for observation in ordered_observations
            ),
            "planned_effective_sample": block["planned_effective_sample"],
            "achieved_effective_sample": sum(
                float(observation["sample"]["effective_sample_size"])
                for observation in ordered_observations
            ),
        },
        "segment_evidence": segment_evidence,
        "comparison_sha256": None,
    }
    document["comparison_sha256"] = sha256_json(document)
    return document
