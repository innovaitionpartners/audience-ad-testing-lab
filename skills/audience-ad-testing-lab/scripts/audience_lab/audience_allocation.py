"""Pure deterministic allocation of frozen audience profiles to stage slots."""

from __future__ import annotations

import copy
from decimal import Decimal, ROUND_FLOOR, localcontext
import hashlib
import json
import math
from typing import Mapping, Sequence


ALLOCATION_REQUEST_VERSION = "audience-profile-allocation-request-v1"
ALLOCATION_PLAN_VERSION = "audience-profile-allocation-plan-v1"
ALLOCATION_SUBSET_VERSION = "audience-profile-allocation-subset-v1"
ALLOCATION_FIDELITY_STATUSES = frozenset(
    {
        "directional_profile_allocation",
        "frame_aligned",
        "allocation_distorted",
    }
)

_STAGES = frozenset({"screening", "boundary", "finalist"})
_ALLOCATION_BASES = frozenset({"directional_planning", "structural_frame"})
_CLAIM_EFFECTS = frozenset(
    {
        "frame_aligned",
        "requires_user_decision",
        "directional_tier_1_for_this_run",
    }
)
_REQUEST_KEYS = {
    "schema_version",
    "stage",
    "stage_roster_id",
    "stable_seed",
    "allocation_basis",
    "slots",
    "profiles",
    "analysis_weights",
    "must_cover_group_ids",
    "maximum_absolute_deviation",
    "allow_directional_allocation",
}
_SLOT_KEYS = {"slot_id", "reported_segment_id"}
_PROFILE_KEYS = {
    "grounded_profile_id",
    "reported_segment_id",
    "structural_group_id",
    "effective_weight",
    "conditional_effective_weight",
    "must_cover_group_ids",
    "profile_snapshot_sha256",
    "eligible",
}
_PLAN_KEYS = {
    "schema_version",
    "stage",
    "stage_roster_id",
    "stable_seed",
    "assignments",
    "profile_diagnostics",
    "structural_group_diagnostics",
    "must_cover_diagnostics",
    "fidelity",
    "claim_effect",
}
_SUBSET_KEYS = {
    "schema_version",
    "stage",
    "stage_roster_id",
    "full_plan_sha256",
    "selected_slot_ids",
    "profile_diagnostics",
    "structural_group_diagnostics",
    "must_cover_diagnostics",
    "fidelity",
    "claim_effect",
}
_ASSIGNMENT_KEYS = {
    "slot_id",
    "grounded_profile_id",
    "reported_segment_id",
    "structural_group_id",
    "profile_snapshot_sha256",
}
_PROFILE_DIAGNOSTIC_KEYS = {
    "grounded_profile_id",
    "reported_segment_id",
    "structural_group_id",
    "profile_snapshot_sha256",
    "eligible",
    "must_cover_group_ids",
    "target_weight",
    "ideal_slot_count",
    "assigned_slots",
    "raw_slot_share",
    "analysis_effective_share",
    "absolute_deviation",
    "matching_floor",
}
_STRUCTURAL_DIAGNOSTIC_KEYS = {
    "structural_group_id",
    "target_weight",
    "assigned_slots",
    "raw_slot_share",
    "analysis_effective_share",
    "absolute_deviation",
}
_MUST_COVER_KEYS = {
    "requested_group_ids",
    "applicable_group_ids",
    "target_weights",
    "covered_group_ids",
    "uncovered_group_ids",
    "matches",
}
_MATCH_KEYS = {
    "must_cover_group_id",
    "slot_id",
    "grounded_profile_id",
    "target_weight",
    "stable_order_sha256",
}
_FIDELITY_KEYS = {
    "allocation_basis",
    "status",
    "maximum_absolute_deviation",
    "observed_maximum_absolute_deviation",
    "all_must_cover_groups_represented",
}
_SHA256_PREFIXED = "sha256:"
_FIVE_POINTS = Decimal("0.05")
_FLOAT_TOLERANCE = 1e-12


def _copy(value: object) -> object:
    return copy.deepcopy(value)


def _require_object(value: object, label: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{label} must be an object")
    return value


def _require_exact_keys(
    value: Mapping[str, object],
    expected: set[str],
    label: str,
) -> None:
    if set(value) != expected:
        raise ValueError(f"{label} keys must match the contract exactly")


def _require_string(value: object, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{label} must be a non-empty string")
    return value


def _require_bool(value: object, label: str) -> bool:
    if not isinstance(value, bool):
        raise ValueError(f"{label} must be a boolean")
    return value


def _require_number(value: object, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{label} must be a finite number")
    numeric = float(value)
    if not math.isfinite(numeric):
        raise ValueError(f"{label} must be a finite number")
    return numeric


def _require_nonnegative(value: object, label: str) -> float:
    numeric = _require_number(value, label)
    if numeric < 0:
        raise ValueError(f"{label} must be nonnegative")
    return numeric


def _require_nonnegative_integer(value: object, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{label} must be a nonnegative integer")
    return value


def _require_string_array(value: object, label: str) -> list[str]:
    if not isinstance(value, list):
        raise ValueError(f"{label} must be an array")
    result = [
        _require_string(item, f"{label}[{index}]")
        for index, item in enumerate(value)
    ]
    if len(result) != len(set(result)):
        raise ValueError(f"{label} must not contain duplicates")
    return result


def _require_hash(value: object, label: str) -> str:
    digest = _require_string(value, label)
    if (
        not digest.startswith(_SHA256_PREFIXED)
        or len(digest) != len(_SHA256_PREFIXED) + 64
        or any(character not in "0123456789abcdef" for character in digest[7:])
    ):
        raise ValueError(f"{label} must be a lowercase prefixed SHA-256 digest")
    return digest


def _decimal(value: object) -> Decimal:
    return Decimal(str(value))


def _same_number(left: object, right: object) -> bool:
    try:
        return math.isclose(
            float(left),
            float(right),
            rel_tol=0.0,
            abs_tol=_FLOAT_TOLERANCE,
        )
    except (TypeError, ValueError):
        return False


def _stable_digest(
    stable_seed: str,
    stage_roster_id: str,
    slot_id: str,
    profile_id: str,
) -> str:
    raw = (
        stable_seed
        + "\0"
        + stage_roster_id
        + "\0"
        + slot_id
        + "\0"
        + profile_id
    ).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _canonical_sha256(value: object) -> str:
    raw = (
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        + "\n"
    ).encode("utf-8")
    return _SHA256_PREFIXED + hashlib.sha256(raw).hexdigest()


def validate_allocation_request(payload: object) -> dict[str, object]:
    """Validate and defensively copy one strict stage allocation request."""

    request = _require_object(payload, "allocation request")
    _require_exact_keys(request, _REQUEST_KEYS, "allocation request")
    if request["schema_version"] != ALLOCATION_REQUEST_VERSION:
        raise ValueError("allocation request schema_version is unsupported")
    stage = _require_string(request["stage"], "stage")
    if stage not in _STAGES:
        raise ValueError("stage is unsupported")
    _require_string(request["stage_roster_id"], "stage_roster_id")
    _require_string(request["stable_seed"], "stable_seed")
    basis = _require_string(request["allocation_basis"], "allocation_basis")
    if basis not in _ALLOCATION_BASES:
        raise ValueError("allocation_basis is unsupported")
    maximum = _require_nonnegative(
        request["maximum_absolute_deviation"],
        "maximum_absolute_deviation",
    )
    if _decimal(maximum) != _FIVE_POINTS:
        raise ValueError("maximum_absolute_deviation must be exactly 0.05")
    _require_bool(
        request["allow_directional_allocation"],
        "allow_directional_allocation",
    )

    raw_slots = request["slots"]
    if not isinstance(raw_slots, list) or not raw_slots:
        raise ValueError("slots must be a non-empty array")
    slot_ids: list[str] = []
    slot_segments: list[str] = []
    for index, raw_slot in enumerate(raw_slots):
        slot = _require_object(raw_slot, f"slots[{index}]")
        _require_exact_keys(slot, _SLOT_KEYS, f"slots[{index}]")
        slot_ids.append(_require_string(slot["slot_id"], f"slots[{index}].slot_id"))
        segment = slot["reported_segment_id"]
        if stage == "finalist":
            if segment is not None:
                raise ValueError("finalist slots must have null reported_segment_id")
        else:
            slot_segments.append(
                _require_string(
                    segment,
                    f"slots[{index}].reported_segment_id",
                )
            )
    if len(slot_ids) != len(set(slot_ids)):
        raise ValueError("slot IDs must be unique")

    requested_groups = _require_string_array(
        request["must_cover_group_ids"],
        "must_cover_group_ids",
    )
    raw_profiles = request["profiles"]
    if not isinstance(raw_profiles, list) or not raw_profiles:
        raise ValueError("profiles must be a non-empty array")
    profile_ids: list[str] = []
    profile_segments: list[str] = []
    eligible_by_segment: dict[str, int] = {}
    conditional_total_by_segment: dict[str, Decimal] = {}
    finalist_effective_total = Decimal(0)
    for index, raw_profile in enumerate(raw_profiles):
        profile_value = _require_object(raw_profile, f"profiles[{index}]")
        _require_exact_keys(profile_value, _PROFILE_KEYS, f"profiles[{index}]")
        profile_id = _require_string(
            profile_value["grounded_profile_id"],
            f"profiles[{index}].grounded_profile_id",
        )
        profile_ids.append(profile_id)
        segment_id = _require_string(
            profile_value["reported_segment_id"],
            f"profiles[{index}].reported_segment_id",
        )
        profile_segments.append(segment_id)
        _require_string(
            profile_value["structural_group_id"],
            f"profiles[{index}].structural_group_id",
        )
        effective = _require_nonnegative(
            profile_value["effective_weight"],
            f"profiles[{index}].effective_weight",
        )
        conditional = _require_nonnegative(
            profile_value["conditional_effective_weight"],
            f"profiles[{index}].conditional_effective_weight",
        )
        profile_groups = _require_string_array(
            profile_value["must_cover_group_ids"],
            f"profiles[{index}].must_cover_group_ids",
        )
        if not set(profile_groups).issubset(requested_groups):
            raise ValueError("profile must-cover groups must be declared by the request")
        _require_hash(
            profile_value["profile_snapshot_sha256"],
            f"profiles[{index}].profile_snapshot_sha256",
        )
        eligible = _require_bool(
            profile_value["eligible"],
            f"profiles[{index}].eligible",
        )
        if eligible:
            eligible_by_segment[segment_id] = eligible_by_segment.get(segment_id, 0) + 1
            conditional_total_by_segment[segment_id] = (
                conditional_total_by_segment.get(segment_id, Decimal(0))
                + _decimal(conditional)
            )
            finalist_effective_total += _decimal(effective)
    if len(profile_ids) != len(set(profile_ids)):
        raise ValueError("grounded profile IDs must be unique")

    raw_analysis = request["analysis_weights"]
    if not isinstance(raw_analysis, Mapping):
        raise ValueError("analysis_weights must be an object")
    analysis_weights: dict[str, float] = {}
    for raw_segment_id, raw_weight in raw_analysis.items():
        segment_id = _require_string(raw_segment_id, "analysis_weights key")
        analysis_weights[segment_id] = _require_nonnegative(
            raw_weight,
            f"analysis_weights.{segment_id}",
        )
    if stage == "finalist":
        if analysis_weights:
            raise ValueError("finalist analysis_weights must be empty")
        if finalist_effective_total <= 0:
            raise ValueError("finalist eligible effective weights must have positive total")
    else:
        exact_segments = set(slot_segments)
        if set(analysis_weights) != exact_segments:
            raise ValueError(
                "analysis_weights must exactly reference the existing slot segments"
            )
        if not math.isclose(
            sum(analysis_weights.values()),
            1.0,
            rel_tol=0.0,
            abs_tol=_FLOAT_TOLERANCE,
        ):
            raise ValueError("analysis_weights must sum to 1")
        if not set(profile_segments).issubset(exact_segments):
            raise ValueError("profiles reference a segment absent from the stage roster")
        for segment_id in exact_segments:
            if eligible_by_segment.get(segment_id, 0) == 0:
                raise ValueError("every occupied segment needs an eligible profile")
            if conditional_total_by_segment.get(segment_id, Decimal(0)) <= 0:
                raise ValueError(
                    "every occupied segment needs positive eligible conditional weight"
                )
    return _copy(dict(request))  # type: ignore[return-value]


def _profile_targets(
    request: Mapping[str, object],
) -> tuple[
    dict[str, Decimal],
    dict[str, Decimal],
    dict[str, Decimal],
]:
    profiles = request["profiles"]
    slots = request["slots"]
    stage = request["stage"]
    target: dict[str, Decimal] = {
        str(item["grounded_profile_id"]): Decimal(0) for item in profiles
    }
    ideal: dict[str, Decimal] = {
        str(item["grounded_profile_id"]): Decimal(0) for item in profiles
    }
    segment_mass: dict[str, Decimal] = {}
    with localcontext() as context:
        context.prec = 50
        if stage == "finalist":
            eligible = [item for item in profiles if item["eligible"]]
            total = sum(
                (_decimal(item["effective_weight"]) for item in eligible),
                Decimal(0),
            )
            for item in eligible:
                profile_id = str(item["grounded_profile_id"])
                share = _decimal(item["effective_weight"]) / total
                target[profile_id] = share
                ideal[profile_id] = share * len(slots)
            return target, ideal, segment_mass

        analysis = request["analysis_weights"]
        for segment_id, raw_mass in analysis.items():
            mass = _decimal(raw_mass)
            segment_mass[str(segment_id)] = mass
            segment_profiles = [
                item
                for item in profiles
                if item["eligible"] and item["reported_segment_id"] == segment_id
            ]
            segment_slots = [
                item for item in slots if item["reported_segment_id"] == segment_id
            ]
            total = sum(
                (
                    _decimal(item["conditional_effective_weight"])
                    for item in segment_profiles
                ),
                Decimal(0),
            )
            for item in segment_profiles:
                profile_id = str(item["grounded_profile_id"])
                conditional = _decimal(item["conditional_effective_weight"]) / total
                target[profile_id] = mass * conditional
                ideal[profile_id] = len(segment_slots) * conditional
    return target, ideal, segment_mass


def _group_target_weights(
    request: Mapping[str, object],
    profile_targets: Mapping[str, Decimal],
) -> dict[str, Decimal]:
    result: dict[str, Decimal] = {}
    for group_id in request["must_cover_group_ids"]:
        result[str(group_id)] = sum(
            (
                profile_targets[str(profile["grounded_profile_id"])]
                for profile in request["profiles"]
                if profile["eligible"]
                and group_id in profile["must_cover_group_ids"]
            ),
            Decimal(0),
        )
    return result


def _matching_value(
    groups: Sequence[str],
    slots: Sequence[str],
    adjacency: Mapping[str, set[str]],
    benefits: Mapping[str, int],
) -> int:
    if not groups or not slots:
        return 0
    source = 0
    group_offset = 1
    slot_offset = group_offset + len(groups)
    sink = slot_offset + len(slots)
    node_count = sink + 1
    graph: list[list[list[int]]] = [[] for _ in range(node_count)]

    def add_edge(left: int, right: int, capacity: int, cost: int) -> None:
        graph[left].append([right, len(graph[right]), capacity, cost])
        graph[right].append([left, len(graph[left]) - 1, 0, -cost])

    slot_index = {slot_id: index for index, slot_id in enumerate(slots)}
    for group_index, group_id in enumerate(groups):
        group_node = group_offset + group_index
        add_edge(source, group_node, 1, -benefits[group_id])
        for slot_id in sorted(adjacency.get(group_id, set())):
            if slot_id in slot_index:
                add_edge(group_node, slot_offset + slot_index[slot_id], 1, 0)
    for index in range(len(slots)):
        add_edge(slot_offset + index, sink, 1, 0)

    total_cost = 0
    infinity = sum(benefits.values()) + 1
    while True:
        distance = [infinity] * node_count
        parent: list[tuple[int, int] | None] = [None] * node_count
        distance[source] = 0
        for _ in range(node_count - 1):
            changed = False
            for node, edges in enumerate(graph):
                if distance[node] == infinity:
                    continue
                for edge_index, edge in enumerate(edges):
                    target_node, _reverse, capacity, cost = edge
                    candidate = distance[node] + cost
                    if capacity and candidate < distance[target_node]:
                        distance[target_node] = candidate
                        parent[target_node] = (node, edge_index)
                        changed = True
            if not changed:
                break
        if parent[sink] is None or distance[sink] >= 0:
            break
        node = sink
        while node != source:
            previous, edge_index = parent[node]  # type: ignore[misc]
            edge = graph[previous][edge_index]
            edge[2] -= 1
            graph[node][edge[1]][2] += 1
            node = previous
        total_cost += distance[sink]
    return -total_cost


def _maximum_coverage_matching(
    request: Mapping[str, object],
    group_targets: Mapping[str, Decimal],
) -> list[dict[str, object]]:
    groups = [str(item) for item in request["must_cover_group_ids"]]
    if not groups:
        return []
    slots = [str(item["slot_id"]) for item in request["slots"]]
    profiles = [item for item in request["profiles"] if item["eligible"]]
    stable_seed = str(request["stable_seed"])
    roster_id = str(request["stage_roster_id"])
    stage = str(request["stage"])
    candidates: list[tuple[str, str, str, str]] = []
    for group_id in groups:
        for slot in request["slots"]:
            slot_id = str(slot["slot_id"])
            for profile in profiles:
                if group_id not in profile["must_cover_group_ids"]:
                    continue
                if (
                    stage != "finalist"
                    and profile["reported_segment_id"] != slot["reported_segment_id"]
                ):
                    continue
                profile_id = str(profile["grounded_profile_id"])
                candidates.append(
                    (
                        _stable_digest(stable_seed, roster_id, slot_id, profile_id),
                        group_id,
                        slot_id,
                        profile_id,
                    )
                )
    best_candidate_by_edge: dict[
        tuple[str, str],
        tuple[str, str, str, str],
    ] = {}
    for candidate in candidates:
        edge = (candidate[1], candidate[2])
        if edge not in best_candidate_by_edge or candidate < best_candidate_by_edge[edge]:
            best_candidate_by_edge[edge] = candidate
    candidates = list(best_candidate_by_edge.values())
    candidates.sort()
    exponents = [
        max(0, -group_targets[group_id].as_tuple().exponent) for group_id in groups
    ]
    scale = Decimal(10) ** max(exponents, default=0)
    benefits = {
        group_id: int(group_targets[group_id] * scale) * (len(groups) + 1) + 1
        for group_id in groups
    }

    def optimum(
        available_groups: Sequence[str],
        available_slots: Sequence[str],
        available_candidates: Sequence[tuple[str, str, str, str]],
    ) -> int:
        adjacency = {group_id: set() for group_id in available_groups}
        allowed_groups = set(available_groups)
        allowed_slots = set(available_slots)
        for _digest, group_id, slot_id, _profile_id in available_candidates:
            if group_id in allowed_groups and slot_id in allowed_slots:
                adjacency[group_id].add(slot_id)
        return _matching_value(
            available_groups,
            available_slots,
            adjacency,
            benefits,
        )

    target_value = optimum(groups, slots, candidates)
    forced_value = 0
    chosen: list[tuple[str, str, str, str]] = []
    used_groups: set[str] = set()
    used_slots: set[str] = set()
    last_key: tuple[str, str, str, str] | None = None
    while forced_value < target_value:
        selected: tuple[str, str, str, str] | None = None
        for candidate in candidates:
            digest, group_id, slot_id, _profile_id = candidate
            if last_key is not None and candidate <= last_key:
                continue
            if group_id in used_groups or slot_id in used_slots:
                continue
            next_groups = [
                item
                for item in groups
                if item not in used_groups and item != group_id
            ]
            next_slots = [
                item
                for item in slots
                if item not in used_slots and item != slot_id
            ]
            next_candidates = [
                item
                for item in candidates
                if item > candidate
                and item[1] in next_groups
                and item[2] in next_slots
            ]
            remaining_value = optimum(
                next_groups,
                next_slots,
                next_candidates,
            )
            if forced_value + benefits[group_id] + remaining_value == target_value:
                selected = candidate
                break
        if selected is None:
            raise AssertionError("maximum-coverage matching could not be reconstructed")
        chosen.append(selected)
        last_key = selected
        used_groups.add(selected[1])
        used_slots.add(selected[2])
        forced_value += benefits[selected[1]]
    matches = [
        {
            "must_cover_group_id": group_id,
            "slot_id": slot_id,
            "grounded_profile_id": profile_id,
            "target_weight": float(group_targets[group_id]),
            "stable_order_sha256": _SHA256_PREFIXED + digest,
        }
        for digest, group_id, slot_id, profile_id in chosen
    ]
    matches.sort(key=lambda item: str(item["must_cover_group_id"]))
    return matches


def _count_scope_key(
    request: Mapping[str, object],
    profile: Mapping[str, object],
) -> str:
    if request["stage"] == "finalist":
        return "__global__"
    return str(profile["reported_segment_id"])


def _allocate_scope_counts(
    profile_ids: Sequence[str],
    *,
    capacity: int,
    ideals: Mapping[str, Decimal],
    mandatory_floors: Mapping[str, int],
    tie_slot_ids: Sequence[str],
    stable_seed: str,
    stage_roster_id: str,
) -> dict[str, int]:
    counts = {
        profile_id: max(
            int(ideals[profile_id].to_integral_value(rounding=ROUND_FLOOR)),
            mandatory_floors.get(profile_id, 0),
        )
        for profile_id in profile_ids
    }
    step = 0
    while sum(counts.values()) > capacity:
        removable = [
            profile_id
            for profile_id in profile_ids
            if counts[profile_id] > mandatory_floors.get(profile_id, 0)
        ]
        if not removable:
            raise AssertionError("mandatory floors exceed fixed stage capacity")
        tie_slot_id = tie_slot_ids[step % len(tie_slot_ids)]
        selected = min(
            removable,
            key=lambda profile_id: (
                abs(Decimal(counts[profile_id] - 1) - ideals[profile_id])
                - abs(Decimal(counts[profile_id]) - ideals[profile_id]),
                _stable_digest(
                    stable_seed,
                    stage_roster_id,
                    tie_slot_id,
                    profile_id,
                ),
            ),
        )
        counts[selected] -= 1
        step += 1
    while sum(counts.values()) < capacity:
        tie_slot_id = tie_slot_ids[step % len(tie_slot_ids)]
        selected = min(
            profile_ids,
            key=lambda profile_id: (
                abs(Decimal(counts[profile_id] + 1) - ideals[profile_id])
                - abs(Decimal(counts[profile_id]) - ideals[profile_id]),
                _stable_digest(
                    stable_seed,
                    stage_roster_id,
                    tie_slot_id,
                    profile_id,
                ),
            ),
        )
        counts[selected] += 1
        step += 1
    return counts


def _final_profile_counts(
    request: Mapping[str, object],
    ideals: Mapping[str, Decimal],
    matches: Sequence[Mapping[str, object]],
) -> dict[str, int]:
    profiles = [item for item in request["profiles"] if item["eligible"]]
    floors: dict[str, int] = {}
    for match in matches:
        profile_id = str(match["grounded_profile_id"])
        floors[profile_id] = floors.get(profile_id, 0) + 1
    scopes: dict[str, list[Mapping[str, object]]] = {}
    for profile in profiles:
        scopes.setdefault(_count_scope_key(request, profile), []).append(profile)
    result = {
        str(profile["grounded_profile_id"]): 0 for profile in request["profiles"]
    }
    for scope, scope_profiles in scopes.items():
        scope_slots = [
            item
            for item in request["slots"]
            if request["stage"] == "finalist"
            or item["reported_segment_id"] == scope
        ]
        profile_ids = [str(item["grounded_profile_id"]) for item in scope_profiles]
        allocated = _allocate_scope_counts(
            profile_ids,
            capacity=len(scope_slots),
            ideals=ideals,
            mandatory_floors=floors,
            tie_slot_ids=[str(item["slot_id"]) for item in scope_slots],
            stable_seed=str(request["stable_seed"]),
            stage_roster_id=str(request["stage_roster_id"]),
        )
        result.update(allocated)
    return result


def _assign_profiles(
    request: Mapping[str, object],
    profile_targets: Mapping[str, Decimal],
    ideals: Mapping[str, Decimal],
    counts: Mapping[str, int],
    matches: Sequence[Mapping[str, object]],
) -> list[dict[str, object]]:
    profiles = {
        str(item["grounded_profile_id"]): item for item in request["profiles"]
    }
    anchors = {
        str(match["slot_id"]): str(match["grounded_profile_id"])
        for match in matches
    }
    future_anchors: dict[str, int] = {}
    for profile_id in anchors.values():
        future_anchors[profile_id] = future_anchors.get(profile_id, 0) + 1
    remaining = dict(counts)
    assigned_so_far = {profile_id: 0 for profile_id in profiles}
    seen_by_scope: dict[str, int] = {}
    assignments: list[dict[str, object]] = []
    for slot in request["slots"]:
        slot_id = str(slot["slot_id"])
        scope = (
            "__global__"
            if request["stage"] == "finalist"
            else str(slot["reported_segment_id"])
        )
        seen_by_scope[scope] = seen_by_scope.get(scope, 0) + 1
        if slot_id in anchors:
            selected_id = anchors[slot_id]
            future_anchors[selected_id] -= 1
            if remaining[selected_id] <= 0:
                raise AssertionError("matching anchor exceeds the final profile count")
        else:
            candidates = [
                profile_id
                for profile_id, profile in profiles.items()
                if profile["eligible"]
                and _count_scope_key(request, profile) == scope
                and remaining[profile_id] > future_anchors.get(profile_id, 0)
            ]
            if not candidates:
                raise AssertionError("fixed profile counts cannot fill an existing slot")

            def ordering(profile_id: str) -> tuple[Decimal, str]:
                scope_capacity = sum(
                    request["stage"] == "finalist"
                    or item["reported_segment_id"] == scope
                    for item in request["slots"]
                )
                conditional_target = ideals[profile_id] / scope_capacity
                deficit = (
                    conditional_target * seen_by_scope[scope]
                    - assigned_so_far[profile_id]
                )
                return (
                    -deficit,
                    _stable_digest(
                        str(request["stable_seed"]),
                        str(request["stage_roster_id"]),
                        slot_id,
                        profile_id,
                    ),
                )

            selected_id = min(candidates, key=ordering)
        selected = profiles[selected_id]
        remaining[selected_id] -= 1
        assigned_so_far[selected_id] += 1
        assignments.append(
            {
                "slot_id": slot_id,
                "grounded_profile_id": selected_id,
                "reported_segment_id": (
                    selected["reported_segment_id"]
                    if request["stage"] == "finalist"
                    else slot["reported_segment_id"]
                ),
                "structural_group_id": selected["structural_group_id"],
                "profile_snapshot_sha256": selected["profile_snapshot_sha256"],
            }
        )
    if any(remaining.values()):
        raise AssertionError("fixed profile counts did not reconcile to the stage roster")
    return assignments


def _analysis_profile_shares(
    *,
    stage: str,
    profiles: Sequence[Mapping[str, object]],
    assignments: Sequence[Mapping[str, object]],
    target_weights: Mapping[str, Decimal],
) -> dict[str, Decimal]:
    counts = {
        str(profile["grounded_profile_id"]): 0 for profile in profiles
    }
    for assignment in assignments:
        profile_id = str(assignment["grounded_profile_id"])
        counts[profile_id] = counts.get(profile_id, 0) + 1
    if stage == "finalist":
        return {
            profile_id: Decimal(count) / len(assignments)
            for profile_id, count in counts.items()
        }
    segment_masses: dict[str, Decimal] = {}
    segment_counts: dict[str, int] = {}
    for profile in profiles:
        profile_id = str(profile["grounded_profile_id"])
        segment_id = str(profile["reported_segment_id"])
        segment_masses[segment_id] = (
            segment_masses.get(segment_id, Decimal(0))
            + target_weights[profile_id]
        )
    for assignment in assignments:
        segment_id = str(assignment["reported_segment_id"])
        segment_counts[segment_id] = segment_counts.get(segment_id, 0) + 1
    result: dict[str, Decimal] = {}
    for profile in profiles:
        profile_id = str(profile["grounded_profile_id"])
        segment_id = str(profile["reported_segment_id"])
        denominator = segment_counts.get(segment_id, 0)
        result[profile_id] = (
            segment_masses[segment_id] * counts[profile_id] / denominator
            if denominator
            else Decimal(0)
        )
    return result


def _diagnostics(
    *,
    stage: str,
    profiles: Sequence[Mapping[str, object]],
    assignments: Sequence[Mapping[str, object]],
    target_weights: Mapping[str, Decimal],
    ideal_counts: Mapping[str, Decimal],
    requested_groups: Sequence[str],
    group_target_weights: Mapping[str, Decimal],
    matches: Sequence[Mapping[str, object]],
) -> tuple[
    list[dict[str, object]],
    list[dict[str, object]],
    dict[str, object],
]:
    total_slots = len(assignments)
    counts = {
        str(profile["grounded_profile_id"]): 0 for profile in profiles
    }
    for assignment in assignments:
        counts[str(assignment["grounded_profile_id"])] += 1
    analysis_shares = _analysis_profile_shares(
        stage=stage,
        profiles=profiles,
        assignments=assignments,
        target_weights=target_weights,
    )
    floors: dict[str, int] = {}
    for match in matches:
        profile_id = str(match["grounded_profile_id"])
        floors[profile_id] = floors.get(profile_id, 0) + 1
    profile_diagnostics = []
    for profile in sorted(
        profiles,
        key=lambda item: str(item["grounded_profile_id"]),
    ):
        profile_id = str(profile["grounded_profile_id"])
        target = target_weights[profile_id]
        actual = analysis_shares[profile_id]
        profile_diagnostics.append(
            {
                "grounded_profile_id": profile_id,
                "reported_segment_id": profile["reported_segment_id"],
                "structural_group_id": profile["structural_group_id"],
                "profile_snapshot_sha256": profile["profile_snapshot_sha256"],
                "eligible": profile["eligible"],
                "must_cover_group_ids": _copy(profile["must_cover_group_ids"]),
                "target_weight": float(target),
                "ideal_slot_count": float(ideal_counts[profile_id]),
                "assigned_slots": counts[profile_id],
                "raw_slot_share": counts[profile_id] / total_slots,
                "analysis_effective_share": float(actual),
                "absolute_deviation": float(abs(actual - target)),
                "matching_floor": floors.get(profile_id, 0),
            }
        )

    profile_by_id = {
        str(profile["grounded_profile_id"]): profile for profile in profiles
    }
    structural_group_ids = sorted(
        {str(profile["structural_group_id"]) for profile in profiles}
    )
    structural_diagnostics = []
    for group_id in structural_group_ids:
        group_profile_ids = [
            profile_id
            for profile_id, profile in profile_by_id.items()
            if profile["structural_group_id"] == group_id
        ]
        target = sum(
            (target_weights[profile_id] for profile_id in group_profile_ids),
            Decimal(0),
        )
        assigned = sum(counts[profile_id] for profile_id in group_profile_ids)
        analysis_share = sum(
            (analysis_shares[profile_id] for profile_id in group_profile_ids),
            Decimal(0),
        )
        structural_diagnostics.append(
            {
                "structural_group_id": group_id,
                "target_weight": float(target),
                "assigned_slots": assigned,
                "raw_slot_share": assigned / total_slots,
                "analysis_effective_share": float(analysis_share),
                "absolute_deviation": float(abs(analysis_share - target)),
            }
        )

    covered = sorted(
        {
            str(match["must_cover_group_id"])
            for match in matches
            if match["must_cover_group_id"] in requested_groups
        }
    )
    requested = list(requested_groups)
    must_cover_diagnostics = {
        "requested_group_ids": requested,
        "applicable_group_ids": requested,
        "target_weights": {
            group_id: float(group_target_weights[group_id])
            for group_id in requested
        },
        "covered_group_ids": covered,
        "uncovered_group_ids": [
            group_id for group_id in requested if group_id not in covered
        ],
        "matches": _copy(list(matches)),
    }
    return (
        profile_diagnostics,
        structural_diagnostics,
        must_cover_diagnostics,
    )


def _fidelity_and_claim(
    *,
    allocation_basis: str,
    structural_diagnostics: Sequence[Mapping[str, object]],
    must_cover_diagnostics: Mapping[str, object],
    allow_directional_allocation: bool,
) -> tuple[dict[str, object], str]:
    maximum = max(
        (
            _decimal(item["absolute_deviation"])
            for item in structural_diagnostics
        ),
        default=Decimal(0),
    )
    all_covered = not must_cover_diagnostics["uncovered_group_ids"]
    if allocation_basis == "directional_planning":
        status = "directional_profile_allocation"
        claim = (
            "directional_tier_1_for_this_run"
            if all_covered or allow_directional_allocation
            else "requires_user_decision"
        )
    else:
        aligned = all_covered and maximum <= _FIVE_POINTS
        status = "frame_aligned" if aligned else "allocation_distorted"
        claim = (
            "frame_aligned"
            if aligned
            else (
                "directional_tier_1_for_this_run"
                if allow_directional_allocation
                else "requires_user_decision"
            )
        )
    return (
        {
            "allocation_basis": allocation_basis,
            "status": status,
            "maximum_absolute_deviation": float(_FIVE_POINTS),
            "observed_maximum_absolute_deviation": float(maximum),
            "all_must_cover_groups_represented": all_covered,
        },
        claim,
    )


def allocate_stage_profiles(payload: object) -> dict[str, object]:
    """Allocate every existing stage slot without mutating reusable weights."""

    request = validate_allocation_request(payload)
    profile_targets, ideal_counts, _segment_mass = _profile_targets(request)
    group_targets = _group_target_weights(request, profile_targets)
    matches = _maximum_coverage_matching(request, group_targets)
    counts = _final_profile_counts(request, ideal_counts, matches)
    assignments = _assign_profiles(
        request,
        profile_targets,
        ideal_counts,
        counts,
        matches,
    )
    profile_diagnostics, structural_diagnostics, must_cover_diagnostics = (
        _diagnostics(
            stage=str(request["stage"]),
            profiles=request["profiles"],
            assignments=assignments,
            target_weights=profile_targets,
            ideal_counts=ideal_counts,
            requested_groups=request["must_cover_group_ids"],
            group_target_weights=group_targets,
            matches=matches,
        )
    )
    fidelity, claim_effect = _fidelity_and_claim(
        allocation_basis=str(request["allocation_basis"]),
        structural_diagnostics=structural_diagnostics,
        must_cover_diagnostics=must_cover_diagnostics,
        allow_directional_allocation=bool(
            request["allow_directional_allocation"]
        ),
    )
    plan = {
        "schema_version": ALLOCATION_PLAN_VERSION,
        "stage": request["stage"],
        "stage_roster_id": request["stage_roster_id"],
        "stable_seed": request["stable_seed"],
        "assignments": assignments,
        "profile_diagnostics": profile_diagnostics,
        "structural_group_diagnostics": structural_diagnostics,
        "must_cover_diagnostics": must_cover_diagnostics,
        "fidelity": fidelity,
        "claim_effect": claim_effect,
    }
    return validate_allocation_plan(plan)


def _validate_profile_diagnostics(
    value: object,
    *,
    total_slots: int,
) -> list[Mapping[str, object]]:
    if not isinstance(value, list) or not value:
        raise ValueError("profile_diagnostics must be a non-empty array")
    profile_ids: list[str] = []
    result: list[Mapping[str, object]] = []
    for index, raw_diagnostic in enumerate(value):
        diagnostic = _require_object(
            raw_diagnostic,
            f"profile_diagnostics[{index}]",
        )
        _require_exact_keys(
            diagnostic,
            _PROFILE_DIAGNOSTIC_KEYS,
            f"profile_diagnostics[{index}]",
        )
        profile_ids.append(
            _require_string(
                diagnostic["grounded_profile_id"],
                f"profile_diagnostics[{index}].grounded_profile_id",
            )
        )
        _require_string(
            diagnostic["reported_segment_id"],
            f"profile_diagnostics[{index}].reported_segment_id",
        )
        _require_string(
            diagnostic["structural_group_id"],
            f"profile_diagnostics[{index}].structural_group_id",
        )
        _require_hash(
            diagnostic["profile_snapshot_sha256"],
            f"profile_diagnostics[{index}].profile_snapshot_sha256",
        )
        _require_bool(
            diagnostic["eligible"],
            f"profile_diagnostics[{index}].eligible",
        )
        _require_string_array(
            diagnostic["must_cover_group_ids"],
            f"profile_diagnostics[{index}].must_cover_group_ids",
        )
        for key in (
            "target_weight",
            "ideal_slot_count",
            "raw_slot_share",
            "analysis_effective_share",
            "absolute_deviation",
        ):
            _require_nonnegative(
                diagnostic[key],
                f"profile_diagnostics[{index}].{key}",
            )
        assigned = _require_nonnegative_integer(
            diagnostic["assigned_slots"],
            f"profile_diagnostics[{index}].assigned_slots",
        )
        _require_nonnegative_integer(
            diagnostic["matching_floor"],
            f"profile_diagnostics[{index}].matching_floor",
        )
        if assigned > total_slots:
            raise ValueError("profile assigned_slots exceed selected capacity")
        if int(diagnostic["matching_floor"]) > assigned:
            raise ValueError("profile matching_floor exceeds assigned slots")
        if diagnostic["eligible"] is False and (
            assigned
            or int(diagnostic["matching_floor"])
            or not _same_number(diagnostic["target_weight"], 0)
            or not _same_number(diagnostic["ideal_slot_count"], 0)
        ):
            raise ValueError("ineligible profiles cannot carry allocation weight or slots")
        if not _same_number(
            diagnostic["raw_slot_share"],
            assigned / total_slots,
        ):
            raise ValueError("profile raw_slot_share does not reconcile")
        result.append(diagnostic)
    if len(profile_ids) != len(set(profile_ids)):
        raise ValueError("profile diagnostics must have unique profile IDs")
    if profile_ids != sorted(profile_ids):
        raise ValueError("profile diagnostics must be sorted by profile ID")
    if not _same_number(
        sum(_decimal(item["target_weight"]) for item in result),
        1,
    ):
        raise ValueError("profile target weights must sum to 1")
    return result


def _validate_diagnostic_bundle(
    *,
    stage: str,
    total_slots: int,
    profile_diagnostics: object,
    structural_group_diagnostics: object,
    must_cover_diagnostics: object,
    fidelity: object,
    claim_effect: object,
) -> tuple[
    list[Mapping[str, object]],
    list[Mapping[str, object]],
    Mapping[str, object],
    Mapping[str, object],
]:
    profiles = _validate_profile_diagnostics(
        profile_diagnostics,
        total_slots=total_slots,
    )
    if sum(int(item["assigned_slots"]) for item in profiles) != total_slots:
        raise ValueError("profile diagnostics do not fill exact selected capacity")
    if stage == "finalist":
        expected_analysis = {
            str(item["grounded_profile_id"]): Decimal(
                int(item["assigned_slots"])
            )
            / total_slots
            for item in profiles
        }
        expected_ideals = {
            str(item["grounded_profile_id"]): (
                _decimal(item["target_weight"]) * total_slots
            )
            for item in profiles
        }
    else:
        segment_masses: dict[str, Decimal] = {}
        segment_counts: dict[str, int] = {}
        for item in profiles:
            segment_id = str(item["reported_segment_id"])
            segment_masses[segment_id] = (
                segment_masses.get(segment_id, Decimal(0))
                + _decimal(item["target_weight"])
            )
            segment_counts[segment_id] = (
                segment_counts.get(segment_id, 0)
                + int(item["assigned_slots"])
            )
        expected_analysis = {}
        for item in profiles:
            profile_id = str(item["grounded_profile_id"])
            segment_id = str(item["reported_segment_id"])
            denominator = segment_counts[segment_id]
            expected_analysis[profile_id] = (
                segment_masses[segment_id]
                * int(item["assigned_slots"])
                / denominator
                if denominator
                else Decimal(0)
            )
        expected_ideals = {}
        for item in profiles:
            profile_id = str(item["grounded_profile_id"])
            segment_id = str(item["reported_segment_id"])
            expected_ideals[profile_id] = (
                _decimal(item["target_weight"])
                / segment_masses[segment_id]
                * segment_counts[segment_id]
                if segment_masses[segment_id]
                else _decimal(item["ideal_slot_count"])
            )
        for segment_id, selected_capacity in segment_counts.items():
            ideal_total = sum(
                (
                    _decimal(item["ideal_slot_count"])
                    for item in profiles
                    if item["reported_segment_id"] == segment_id
                ),
                Decimal(0),
            )
            if not _same_number(ideal_total, selected_capacity):
                raise ValueError(
                    "conditional ideal slot counts do not fill selected capacity"
                )
    for item in profiles:
        profile_id = str(item["grounded_profile_id"])
        if not _same_number(
            item["ideal_slot_count"],
            expected_ideals[profile_id],
        ):
            raise ValueError("profile ideal_slot_count does not reconcile")
        if not _same_number(
            item["analysis_effective_share"],
            expected_analysis[profile_id],
        ):
            raise ValueError("profile analysis_effective_share does not reconcile")
        expected_deviation = abs(
            expected_analysis[profile_id] - _decimal(item["target_weight"])
        )
        if not _same_number(item["absolute_deviation"], expected_deviation):
            raise ValueError("profile absolute_deviation does not reconcile")

    raw_structural = structural_group_diagnostics
    if not isinstance(raw_structural, list) or not raw_structural:
        raise ValueError("structural_group_diagnostics must be a non-empty array")
    structural_ids: list[str] = []
    structural: list[Mapping[str, object]] = []
    for index, raw_item in enumerate(raw_structural):
        item = _require_object(
            raw_item,
            f"structural_group_diagnostics[{index}]",
        )
        _require_exact_keys(
            item,
            _STRUCTURAL_DIAGNOSTIC_KEYS,
            f"structural_group_diagnostics[{index}]",
        )
        group_id = _require_string(
            item["structural_group_id"],
            f"structural_group_diagnostics[{index}].structural_group_id",
        )
        structural_ids.append(group_id)
        group_profiles = [
            profile
            for profile in profiles
            if profile["structural_group_id"] == group_id
        ]
        if not group_profiles:
            raise ValueError("structural diagnostic references no profile")
        assigned = _require_nonnegative_integer(
            item["assigned_slots"],
            f"structural_group_diagnostics[{index}].assigned_slots",
        )
        for key in (
            "target_weight",
            "raw_slot_share",
            "analysis_effective_share",
            "absolute_deviation",
        ):
            _require_nonnegative(
                item[key],
                f"structural_group_diagnostics[{index}].{key}",
            )
        expected_target = sum(
            (_decimal(profile["target_weight"]) for profile in group_profiles),
            Decimal(0),
        )
        expected_assigned = sum(
            int(profile["assigned_slots"]) for profile in group_profiles
        )
        expected_analysis_share = sum(
            (
                _decimal(profile["analysis_effective_share"])
                for profile in group_profiles
            ),
            Decimal(0),
        )
        if assigned != expected_assigned:
            raise ValueError("structural assigned_slots do not reconcile")
        if not _same_number(item["target_weight"], expected_target):
            raise ValueError("structural target_weight does not reconcile")
        if not _same_number(item["raw_slot_share"], assigned / total_slots):
            raise ValueError("structural raw_slot_share does not reconcile")
        if not _same_number(
            item["analysis_effective_share"],
            expected_analysis_share,
        ):
            raise ValueError("structural analysis_effective_share does not reconcile")
        if not _same_number(
            item["absolute_deviation"],
            abs(expected_analysis_share - expected_target),
        ):
            raise ValueError("structural absolute_deviation does not reconcile")
        structural.append(item)
    expected_structural_ids = {
        str(item["structural_group_id"]) for item in profiles
    }
    if set(structural_ids) != expected_structural_ids or len(structural_ids) != len(
        set(structural_ids)
    ):
        raise ValueError("structural diagnostics must cover each group exactly once")
    if structural_ids != sorted(structural_ids):
        raise ValueError("structural diagnostics must be sorted by group ID")

    must_cover = _require_object(
        must_cover_diagnostics,
        "must_cover_diagnostics",
    )
    _require_exact_keys(must_cover, _MUST_COVER_KEYS, "must_cover_diagnostics")
    requested = _require_string_array(
        must_cover["requested_group_ids"],
        "must_cover_diagnostics.requested_group_ids",
    )
    applicable = _require_string_array(
        must_cover["applicable_group_ids"],
        "must_cover_diagnostics.applicable_group_ids",
    )
    if applicable != requested:
        raise ValueError("applicable must-cover groups must retain request order")
    target_weights = _require_object(
        must_cover["target_weights"],
        "must_cover_diagnostics.target_weights",
    )
    if set(target_weights) != set(requested):
        raise ValueError("must-cover target weights must cover requested groups")
    for group_id, weight in target_weights.items():
        _require_nonnegative(
            weight,
            f"must_cover_diagnostics.target_weights.{group_id}",
        )
        expected_target = sum(
            (
                _decimal(item["target_weight"])
                for item in profiles
                if item["eligible"] and group_id in item["must_cover_group_ids"]
            ),
            Decimal(0),
        )
        if not _same_number(weight, expected_target):
            raise ValueError("must-cover target weight does not reconcile")
    if any(
        not set(item["must_cover_group_ids"]).issubset(requested)
        for item in profiles
    ):
        raise ValueError("profile diagnostics contain undeclared must-cover groups")
    covered = _require_string_array(
        must_cover["covered_group_ids"],
        "must_cover_diagnostics.covered_group_ids",
    )
    uncovered = _require_string_array(
        must_cover["uncovered_group_ids"],
        "must_cover_diagnostics.uncovered_group_ids",
    )
    if not set(covered).issubset(requested):
        raise ValueError("covered must-cover groups were not requested")
    if covered != sorted(covered):
        raise ValueError("covered must-cover groups must be sorted")
    if uncovered != [item for item in requested if item not in covered]:
        raise ValueError("uncovered must-cover groups do not reconcile")
    raw_matches = must_cover["matches"]
    if not isinstance(raw_matches, list):
        raise ValueError("must-cover matches must be an array")
    matches: list[Mapping[str, object]] = []
    matched_groups: list[str] = []
    matched_slots: list[str] = []
    floor_counts: dict[str, int] = {}
    profile_by_id = {
        str(item["grounded_profile_id"]): item for item in profiles
    }
    for index, raw_match in enumerate(raw_matches):
        match = _require_object(
            raw_match,
            f"must_cover_diagnostics.matches[{index}]",
        )
        _require_exact_keys(
            match,
            _MATCH_KEYS,
            f"must_cover_diagnostics.matches[{index}]",
        )
        group_id = _require_string(
            match["must_cover_group_id"],
            f"must_cover_diagnostics.matches[{index}].must_cover_group_id",
        )
        slot_id = _require_string(
            match["slot_id"],
            f"must_cover_diagnostics.matches[{index}].slot_id",
        )
        profile_id = _require_string(
            match["grounded_profile_id"],
            f"must_cover_diagnostics.matches[{index}].grounded_profile_id",
        )
        _require_hash(
            match["stable_order_sha256"],
            f"must_cover_diagnostics.matches[{index}].stable_order_sha256",
        )
        if (
            group_id not in requested
            or profile_id not in profile_by_id
            or group_id not in profile_by_id[profile_id]["must_cover_group_ids"]
        ):
            raise ValueError("must-cover match binding is invalid")
        if not _same_number(match["target_weight"], target_weights[group_id]):
            raise ValueError("must-cover match target weight is inconsistent")
        matched_groups.append(group_id)
        matched_slots.append(slot_id)
        floor_counts[profile_id] = floor_counts.get(profile_id, 0) + 1
        matches.append(match)
    if len(matched_groups) != len(set(matched_groups)) or len(matched_slots) != len(
        set(matched_slots)
    ):
        raise ValueError("must-cover matches must use unique groups and slots")
    if matches != sorted(matches, key=lambda item: str(item["must_cover_group_id"])):
        raise ValueError("must-cover matches must be sorted by group ID")
    if covered != sorted(matched_groups):
        raise ValueError("must-cover coverage does not reconcile to matched floors")
    for profile_id, item in profile_by_id.items():
        if int(item["matching_floor"]) != floor_counts.get(profile_id, 0):
            raise ValueError("profile matching_floor does not reconcile")

    fidelity_value = _require_object(fidelity, "fidelity")
    _require_exact_keys(fidelity_value, _FIDELITY_KEYS, "fidelity")
    basis = _require_string(fidelity_value["allocation_basis"], "fidelity.allocation_basis")
    if basis not in _ALLOCATION_BASES:
        raise ValueError("fidelity allocation basis is unsupported")
    status = _require_string(fidelity_value["status"], "fidelity.status")
    if status not in ALLOCATION_FIDELITY_STATUSES:
        raise ValueError("fidelity status is unsupported")
    if not _same_number(
        fidelity_value["maximum_absolute_deviation"],
        _FIVE_POINTS,
    ):
        raise ValueError("fidelity maximum deviation must be exactly 0.05")
    observed = max(
        (_decimal(item["absolute_deviation"]) for item in structural),
        default=Decimal(0),
    )
    if not _same_number(
        fidelity_value["observed_maximum_absolute_deviation"],
        observed,
    ):
        raise ValueError("fidelity observed deviation does not reconcile")
    all_covered = not uncovered
    if (
        _require_bool(
            fidelity_value["all_must_cover_groups_represented"],
            "fidelity.all_must_cover_groups_represented",
        )
        != all_covered
    ):
        raise ValueError("fidelity must-cover flag does not reconcile")
    expected_status = (
        "directional_profile_allocation"
        if basis == "directional_planning"
        else (
            "frame_aligned"
            if all_covered and observed <= _FIVE_POINTS
            else "allocation_distorted"
        )
    )
    if status != expected_status:
        raise ValueError("fidelity status does not reconcile")
    claim = _require_string(claim_effect, "claim_effect")
    if claim not in _CLAIM_EFFECTS:
        raise ValueError("claim_effect is unsupported")
    if status == "frame_aligned" and claim != "frame_aligned":
        raise ValueError("frame-aligned fidelity requires a frame-aligned claim")
    if status != "frame_aligned" and claim == "frame_aligned":
        raise ValueError("only frame-aligned fidelity can claim frame alignment")
    if status == "directional_profile_allocation" and all_covered:
        if claim != "directional_tier_1_for_this_run":
            raise ValueError("complete directional coverage must remain Tier 1")
    if status == "allocation_distorted" and claim == "frame_aligned":
        raise ValueError("distorted allocation cannot claim frame alignment")
    return profiles, structural, must_cover, fidelity_value


def validate_allocation_plan(payload: object) -> dict[str, object]:
    """Validate a complete immutable allocation plan and its bindings."""

    plan = _require_object(payload, "allocation plan")
    _require_exact_keys(plan, _PLAN_KEYS, "allocation plan")
    if plan["schema_version"] != ALLOCATION_PLAN_VERSION:
        raise ValueError("allocation plan schema_version is unsupported")
    stage = _require_string(plan["stage"], "stage")
    if stage not in _STAGES:
        raise ValueError("allocation plan stage is unsupported")
    roster_id = _require_string(plan["stage_roster_id"], "stage_roster_id")
    stable_seed = _require_string(plan["stable_seed"], "stable_seed")
    raw_assignments = plan["assignments"]
    if not isinstance(raw_assignments, list) or not raw_assignments:
        raise ValueError("assignments must be a non-empty array")
    assignments: list[Mapping[str, object]] = []
    slot_ids: list[str] = []
    for index, raw_assignment in enumerate(raw_assignments):
        assignment = _require_object(raw_assignment, f"assignments[{index}]")
        _require_exact_keys(
            assignment,
            _ASSIGNMENT_KEYS,
            f"assignments[{index}]",
        )
        slot_ids.append(
            _require_string(
                assignment["slot_id"],
                f"assignments[{index}].slot_id",
            )
        )
        _require_string(
            assignment["grounded_profile_id"],
            f"assignments[{index}].grounded_profile_id",
        )
        _require_string(
            assignment["reported_segment_id"],
            f"assignments[{index}].reported_segment_id",
        )
        _require_string(
            assignment["structural_group_id"],
            f"assignments[{index}].structural_group_id",
        )
        _require_hash(
            assignment["profile_snapshot_sha256"],
            f"assignments[{index}].profile_snapshot_sha256",
        )
        assignments.append(assignment)
    if len(slot_ids) != len(set(slot_ids)):
        raise ValueError("assignment slot IDs must be unique")
    profiles, _structural, must_cover, _fidelity = _validate_diagnostic_bundle(
        stage=stage,
        total_slots=len(assignments),
        profile_diagnostics=plan["profile_diagnostics"],
        structural_group_diagnostics=plan["structural_group_diagnostics"],
        must_cover_diagnostics=plan["must_cover_diagnostics"],
        fidelity=plan["fidelity"],
        claim_effect=plan["claim_effect"],
    )
    profile_by_id = {
        str(item["grounded_profile_id"]): item for item in profiles
    }
    counts = {profile_id: 0 for profile_id in profile_by_id}
    assignment_by_slot: dict[str, Mapping[str, object]] = {}
    for assignment in assignments:
        profile_id = str(assignment["grounded_profile_id"])
        profile = profile_by_id.get(profile_id)
        if profile is None:
            raise ValueError("assignment references an unknown profile")
        if (
            assignment["reported_segment_id"] != profile["reported_segment_id"]
            or assignment["structural_group_id"] != profile["structural_group_id"]
            or assignment["profile_snapshot_sha256"]
            != profile["profile_snapshot_sha256"]
        ):
            raise ValueError("assignment profile binding does not reconcile")
        counts[profile_id] += 1
        assignment_by_slot[str(assignment["slot_id"])] = assignment
    for profile_id, count in counts.items():
        if count != profile_by_id[profile_id]["assigned_slots"]:
            raise ValueError("assignment count does not reconcile")
    for match in must_cover["matches"]:
        assignment = assignment_by_slot.get(str(match["slot_id"]))
        if (
            assignment is None
            or assignment["grounded_profile_id"] != match["grounded_profile_id"]
        ):
            raise ValueError("must-cover matching floor is not frozen in its slot")
        expected_digest = _SHA256_PREFIXED + _stable_digest(
            stable_seed,
            roster_id,
            str(match["slot_id"]),
            str(match["grounded_profile_id"]),
        )
        if match["stable_order_sha256"] != expected_digest:
            raise ValueError("must-cover stable ordering hash does not reconcile")
    return _copy(dict(plan))  # type: ignore[return-value]


def _subset_profile_records(
    plan: Mapping[str, object],
    selected_assignments: Sequence[Mapping[str, object]],
) -> list[dict[str, object]]:
    full_profiles = plan["profile_diagnostics"]
    counts = {
        str(item["grounded_profile_id"]): 0 for item in full_profiles
    }
    for assignment in selected_assignments:
        counts[str(assignment["grounded_profile_id"])] += 1
    selected_match_counts: dict[str, int] = {}
    selected_slot_ids = {str(item["slot_id"]) for item in selected_assignments}
    for match in plan["must_cover_diagnostics"]["matches"]:
        if match["slot_id"] in selected_slot_ids:
            profile_id = str(match["grounded_profile_id"])
            selected_match_counts[profile_id] = (
                selected_match_counts.get(profile_id, 0) + 1
            )
    records = []
    for item in full_profiles:
        copied = _copy(dict(item))
        profile_id = str(item["grounded_profile_id"])
        copied["assigned_slots"] = counts[profile_id]
        copied["matching_floor"] = selected_match_counts.get(profile_id, 0)
        records.append(copied)
    if plan["stage"] == "finalist":
        for item in records:
            item["ideal_slot_count"] = float(
                _decimal(item["target_weight"]) * len(selected_assignments)
            )
    else:
        full_segment_capacity: dict[str, int] = {}
        selected_segment_capacity: dict[str, int] = {}
        for assignment in plan["assignments"]:
            segment_id = str(assignment["reported_segment_id"])
            full_segment_capacity[segment_id] = (
                full_segment_capacity.get(segment_id, 0) + 1
            )
        for assignment in selected_assignments:
            segment_id = str(assignment["reported_segment_id"])
            selected_segment_capacity[segment_id] = (
                selected_segment_capacity.get(segment_id, 0) + 1
            )
        for item in records:
            segment_id = str(item["reported_segment_id"])
            item["ideal_slot_count"] = float(
                _decimal(item["ideal_slot_count"])
                / full_segment_capacity[segment_id]
                * selected_segment_capacity.get(segment_id, 0)
            )
    targets = {
        str(item["grounded_profile_id"]): _decimal(item["target_weight"])
        for item in records
    }
    analysis = _analysis_profile_shares(
        stage=str(plan["stage"]),
        profiles=records,
        assignments=selected_assignments,
        target_weights=targets,
    )
    total = len(selected_assignments)
    for item in records:
        profile_id = str(item["grounded_profile_id"])
        item["raw_slot_share"] = item["assigned_slots"] / total
        item["analysis_effective_share"] = float(analysis[profile_id])
        item["absolute_deviation"] = float(
            abs(analysis[profile_id] - targets[profile_id])
        )
    return records


def _build_allocation_subset(
    validated_plan: Mapping[str, object],
    *,
    selected: list[str],
    allow_directional_allocation: bool,
) -> dict[str, object]:
    selected_assignments = validated_plan["assignments"][: len(selected)]
    profile_records = _subset_profile_records(
        validated_plan,
        selected_assignments,
    )
    targets = {
        str(item["grounded_profile_id"]): _decimal(item["target_weight"])
        for item in profile_records
    }
    selected_set = set(selected)
    selected_matches = [
        _copy(match)
        for match in validated_plan["must_cover_diagnostics"]["matches"]
        if match["slot_id"] in selected_set
    ]
    (
        profile_diagnostics,
        structural_diagnostics,
        must_cover_diagnostics,
    ) = _diagnostics(
        stage=str(validated_plan["stage"]),
        profiles=profile_records,
        assignments=selected_assignments,
        target_weights=targets,
        ideal_counts={
            str(item["grounded_profile_id"]): _decimal(item["ideal_slot_count"])
            for item in profile_records
        },
        requested_groups=validated_plan["must_cover_diagnostics"][
            "requested_group_ids"
        ],
        group_target_weights={
            group_id: _decimal(weight)
            for group_id, weight in validated_plan["must_cover_diagnostics"][
                "target_weights"
            ].items()
        },
        matches=selected_matches,
    )
    fidelity, claim_effect = _fidelity_and_claim(
        allocation_basis=str(validated_plan["fidelity"]["allocation_basis"]),
        structural_diagnostics=structural_diagnostics,
        must_cover_diagnostics=must_cover_diagnostics,
        allow_directional_allocation=allow_directional_allocation,
    )
    return {
        "schema_version": ALLOCATION_SUBSET_VERSION,
        "stage": validated_plan["stage"],
        "stage_roster_id": validated_plan["stage_roster_id"],
        "full_plan_sha256": _canonical_sha256(validated_plan),
        "selected_slot_ids": selected,
        "profile_diagnostics": profile_diagnostics,
        "structural_group_diagnostics": structural_diagnostics,
        "must_cover_diagnostics": must_cover_diagnostics,
        "fidelity": fidelity,
        "claim_effect": claim_effect,
    }


def evaluate_allocation_subset(
    plan: object,
    *,
    selected_slot_ids: list[str],
    allow_directional_allocation: bool,
) -> dict[str, object]:
    """Project exact prefix diagnostics from frozen assignments without reallocating."""

    validated = validate_allocation_plan(plan)
    _require_bool(allow_directional_allocation, "allow_directional_allocation")
    selected = _require_string_array(selected_slot_ids, "selected_slot_ids")
    if not selected:
        raise ValueError("selected_slot_ids must not be empty")
    full_ids = [str(item["slot_id"]) for item in validated["assignments"]]
    if selected != full_ids[: len(selected)]:
        raise ValueError(
            "selected slots must be the exact deterministic allocation prefix"
        )
    subset = _build_allocation_subset(
        validated,
        selected=selected,
        allow_directional_allocation=allow_directional_allocation,
    )
    return validate_allocation_subset(subset, plan=validated)


def validate_allocation_subset(
    payload: object,
    *,
    plan: object,
) -> dict[str, object]:
    """Validate one subset projection against its exact immutable full plan."""

    validated_plan = validate_allocation_plan(plan)
    subset = _require_object(payload, "allocation subset")
    _require_exact_keys(subset, _SUBSET_KEYS, "allocation subset")
    if subset["schema_version"] != ALLOCATION_SUBSET_VERSION:
        raise ValueError("allocation subset schema_version is unsupported")
    stage = _require_string(subset["stage"], "stage")
    if stage not in _STAGES:
        raise ValueError("allocation subset stage is unsupported")
    roster_id = _require_string(subset["stage_roster_id"], "stage_roster_id")
    full_plan_sha256 = _require_hash(
        subset["full_plan_sha256"],
        "full_plan_sha256",
    )
    selected = _require_string_array(
        subset["selected_slot_ids"],
        "selected_slot_ids",
    )
    if not selected:
        raise ValueError("selected_slot_ids must not be empty")
    if stage != validated_plan["stage"]:
        raise ValueError("allocation subset stage does not match the frozen plan")
    if roster_id != validated_plan["stage_roster_id"]:
        raise ValueError(
            "allocation subset stage_roster_id does not match the frozen plan"
        )
    if full_plan_sha256 != _canonical_sha256(validated_plan):
        raise ValueError("allocation subset full_plan_sha256 does not match")
    full_ids = [
        str(item["slot_id"]) for item in validated_plan["assignments"]
    ]
    if selected != full_ids[: len(selected)]:
        raise ValueError(
            "selected slots must be the exact deterministic allocation prefix"
        )
    _validate_diagnostic_bundle(
        stage=stage,
        total_slots=len(selected),
        profile_diagnostics=subset["profile_diagnostics"],
        structural_group_diagnostics=subset["structural_group_diagnostics"],
        must_cover_diagnostics=subset["must_cover_diagnostics"],
        fidelity=subset["fidelity"],
        claim_effect=subset["claim_effect"],
    )
    allow_directional_allocation = (
        subset["claim_effect"] == "directional_tier_1_for_this_run"
    )
    expected = _build_allocation_subset(
        validated_plan,
        selected=selected,
        allow_directional_allocation=allow_directional_allocation,
    )
    if (
        subset["must_cover_diagnostics"]["matches"]
        != expected["must_cover_diagnostics"]["matches"]
    ):
        raise ValueError(
            "subset must-cover matches do not match the frozen plan prefix"
        )
    expected_profiles = {
        str(item["grounded_profile_id"]): item
        for item in expected["profile_diagnostics"]
    }
    for item in subset["profile_diagnostics"]:
        profile_id = str(item["grounded_profile_id"])
        expected_profile = expected_profiles.get(profile_id)
        if expected_profile is None or not _same_number(
            item["ideal_slot_count"],
            expected_profile["ideal_slot_count"],
        ):
            raise ValueError(
                "subset profile ideals do not match the frozen plan prefix"
            )
    if dict(subset) != expected:
        raise ValueError(
            "allocation subset diagnostics do not match the frozen plan prefix"
        )
    return _copy(dict(subset))  # type: ignore[return-value]


__all__ = [
    "ALLOCATION_FIDELITY_STATUSES",
    "ALLOCATION_PLAN_VERSION",
    "ALLOCATION_REQUEST_VERSION",
    "ALLOCATION_SUBSET_VERSION",
    "allocate_stage_profiles",
    "evaluate_allocation_subset",
    "validate_allocation_plan",
    "validate_allocation_request",
    "validate_allocation_subset",
]
