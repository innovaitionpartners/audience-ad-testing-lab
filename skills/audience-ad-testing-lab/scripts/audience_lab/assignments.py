"""Deterministic near-balanced four-creative screening assignments."""

from __future__ import annotations

import hashlib
import itertools
import math
from dataclasses import asdict, dataclass
from fractions import Fraction
from typing import Any, Iterable, Iterator, Mapping, Sequence

from .planning import CapacityPlan, ContextStratum


BLOCK_SIZE = 4
_REPAIR_SWAP_LIMIT = 20_000
_POSITION_REPAIR_STEP_LIMIT = 2_000


@dataclass(frozen=True)
class AssignmentJob:
    synthetic_replicate_id: str
    segment_id: str
    variation_ids: tuple[str, str, str, str]
    shown_order: tuple[str, str, str, str]
    inclusion_probability: float
    context_stratum_id: str | None = None


@dataclass(frozen=True)
class AssignmentDiagnostics:
    connected: bool
    one_block_resilient: bool
    exposure_range: int
    position_range: int
    neighbor_count_min: int
    pair_concurrence_max: int
    pair_concurrence_variance: float


@dataclass(frozen=True)
class _BlockObjective:
    """Exact construction objective before the final seeded tie-break."""

    exposure_ranges: tuple[int, ...]
    position_ranges: tuple[int, ...]
    pair_concurrence_maxima: tuple[int, ...]
    pair_concurrence_weights: tuple[int, ...]
    seeded_tie_break: tuple[Any, ...]

    def comparison_key(self) -> tuple[Any, ...]:
        return (
            self.exposure_ranges,
            self.position_ranges,
            self.pair_concurrence_maxima,
            self.pair_concurrence_weights,
            self.seeded_tie_break,
        )


@dataclass(frozen=True)
class AssignmentPlan:
    creative_ids: tuple[str, ...]
    segment_allocations: tuple[tuple[str, int], ...]
    seed: int
    jobs: tuple[AssignmentJob, ...]
    context_strata: tuple[ContextStratum, ...] = ()
    context_stratum_allocations: tuple[tuple[str, str, int], ...] = ()

    def jobs_as_dicts(self) -> list[dict[str, Any]]:
        """Return JSON-compatible job records in dispatch order."""

        records: list[dict[str, Any]] = []
        for job in self.jobs:
            record = {
                "synthetic_replicate_id": job.synthetic_replicate_id,
                "segment_id": job.segment_id,
                "variation_ids": list(job.variation_ids),
                "shown_order": list(job.shown_order),
                "inclusion_probability": job.inclusion_probability,
            }
            if job.context_stratum_id is not None:
                record["context_stratum_id"] = job.context_stratum_id
            records.append(record)
        return records

    def diagnostics_as_dict(self, segment_id: str | None = None) -> dict[str, Any]:
        """Return the public diagnostics fields as a JSON-compatible mapping."""

        return asdict(assignment_diagnostics(self, segment_id=segment_id))

    def as_dict(self) -> dict[str, Any]:
        """Serialize assignments, detailed balance counts, and graph gates."""

        overall = _detailed_diagnostics(self, segment_id=None)
        by_segment = {
            segment_id: _detailed_diagnostics(self, segment_id=segment_id)
            for segment_id, _ in self.segment_allocations
        }
        payload = {
            "block_size": BLOCK_SIZE,
            "assignment_version": "near-balanced-v2",
            "seed": self.seed,
            "segment_allocations": dict(self.segment_allocations),
            "exposure_counts": overall["exposure_counts"],
            "position_counts": overall["position_counts"],
            "neighbor_counts": overall["neighbor_counts"],
            "pair_concurrence": overall["pair_concurrence"],
            "connected": overall["connected"],
            "one_block_resilient": overall["one_block_resilient"],
            "exposure_range": overall["exposure_range"],
            "position_range": overall["position_range"],
            "neighbor_count_min": overall["neighbor_count_min"],
            "pair_concurrence_max": overall["pair_concurrence"]["max"],
            "pair_concurrence_variance": overall["pair_concurrence"]["variance"],
            "by_segment": by_segment,
            "synthetic_replicate_jobs": self.jobs_as_dicts(),
        }
        if self.context_strata:
            payload.update(
                {
                    "context_strata": [
                        stratum.as_dict() for stratum in self.context_strata
                    ],
                    "context_stratum_allocations": [
                        {
                            "segment_id": segment_id,
                            "context_stratum_id": context_stratum_id,
                            "planned_jobs": planned_jobs,
                        }
                        for segment_id, context_stratum_id, planned_jobs
                        in self.context_stratum_allocations
                    ],
                    "context_stratum_balance": [
                        _context_stratum_diagnostics(
                            self,
                            segment_id=segment_id,
                            context_stratum_id=context_stratum_id,
                            planned_jobs=planned_jobs,
                        )
                        for segment_id, context_stratum_id, planned_jobs
                        in self.context_stratum_allocations
                    ],
                }
            )
        return payload


def build_assignments(
    creative_ids: Sequence[str],
    segment_allocations: Mapping[str, int],
    seed: int,
    *,
    capacity_plan: CapacityPlan | None = None,
    context_strata: Sequence[ContextStratum | Mapping[str, Any]] | None = None,
) -> AssignmentPlan:
    """Build deterministic blocks without using any observed study outcomes.

    Segment allocations are planned job counts. A supplied capacity plan must be
    feasible and reserve exactly the same number of screening jobs.
    """

    normalized_ids = _validate_creative_ids(creative_ids)
    allocations = _validate_segment_allocations(segment_allocations)
    normalized_seed = _require_int("seed", seed)
    normalized_strata = _validate_context_strata(context_strata, allocations)
    strata_by_segment = {
        segment_id: tuple(
            stratum
            for stratum in normalized_strata
            if stratum.segment_id == segment_id
        )
        for segment_id, _ in allocations
    }
    planned_jobs = sum(job_count for _, job_count in allocations)

    if capacity_plan is not None:
        if not isinstance(capacity_plan, CapacityPlan):
            raise ValueError("capacity_plan must be a CapacityPlan")
        if not capacity_plan.ceiling_satisfied:
            raise ValueError("capacity ceiling must be satisfied before assignment")
        if capacity_plan.screening_planned != planned_jobs:
            raise ValueError(
                "capacity_plan.screening_planned must equal total segment allocations"
            )

    jobs: list[AssignmentJob] = []
    overall_exposure_counts = {creative_id: 0 for creative_id in normalized_ids}
    overall_position_counts = {
        creative_id: [0 for _ in range(BLOCK_SIZE)] for creative_id in normalized_ids
    }
    overall_pair_counts = {
        pair: 0 for pair in _all_pairs(normalized_ids)
    }
    inclusion_probability = BLOCK_SIZE / len(normalized_ids)
    stratum_allocations: list[tuple[str, str, int]] = []
    for segment_id, job_count in allocations:
        if job_count * BLOCK_SIZE < 2 * len(normalized_ids):
            raise ValueError(
                f"segment {segment_id!r} cannot produce a one-block-resilient graph "
                f"with {job_count} jobs"
            )
        segment_stratum_allocations = _allocate_context_strata(
            strata_by_segment[segment_id],
            job_count=job_count,
            seed=normalized_seed,
        )
        stratum_allocations.extend(
            (segment_id, context_stratum_id, count)
            for context_stratum_id, count in segment_stratum_allocations
        )
        segment_jobs = _build_segment(
            normalized_ids,
            segment_id=segment_id,
            job_count=job_count,
            seed=normalized_seed,
            inclusion_probability=inclusion_probability,
            overall_exposure_counts=overall_exposure_counts,
            overall_position_counts=overall_position_counts,
            overall_pair_counts=overall_pair_counts,
            context_stratum_allocations=segment_stratum_allocations,
        )
        jobs.extend(segment_jobs)

    plan = AssignmentPlan(
        creative_ids=normalized_ids,
        segment_allocations=allocations,
        seed=normalized_seed,
        jobs=tuple(jobs),
        context_strata=normalized_strata,
        context_stratum_allocations=tuple(stratum_allocations),
    )
    for segment_id, _ in allocations:
        diagnostics = assignment_diagnostics(plan, segment_id=segment_id)
        if not diagnostics.connected or not diagnostics.one_block_resilient:
            raise ValueError(
                f"bounded deterministic repair could not produce a resilient "
                f"comparison graph for segment {segment_id!r}"
            )
    return plan


def build_boundary_reserve_slots(
    segment_ids: Sequence[str],
    *,
    jobs_per_wave: int,
    waves_max: int,
) -> list[dict[str, object]]:
    """Return every existing boundary reserve ID in wave/position order."""

    if (
        not isinstance(segment_ids, Sequence)
        or isinstance(segment_ids, (str, bytes))
        or not segment_ids
        or not all(
            isinstance(segment_id, str) and segment_id.strip()
            for segment_id in segment_ids
        )
        or len(set(segment_ids)) != len(segment_ids)
    ):
        raise ValueError("segment_ids must be a non-empty unique sequence")
    jobs_per_wave = _require_int("jobs_per_wave", jobs_per_wave)
    waves_max = _require_int("waves_max", waves_max)
    if jobs_per_wave < 1 or waves_max < 1:
        raise ValueError("boundary reserve dimensions must be positive")
    slots: list[dict[str, object]] = []
    for wave in range(1, waves_max + 1):
        for position in range(1, jobs_per_wave + 1):
            slot_index = len(slots)
            slots.append(
                {
                    "slot_id": (
                        f"boundary-wave-{wave:02d}-job-{position:04d}"
                    ),
                    "reported_segment_id": segment_ids[
                        slot_index % len(segment_ids)
                    ],
                }
            )
    return slots


def build_finalist_reserve_slots(
    finalist_reserved: int,
) -> list[dict[str, object]]:
    """Return every existing global finalist reserve ID in dispatch order."""

    finalist_reserved = _require_int("finalist_reserved", finalist_reserved)
    if finalist_reserved < 1:
        raise ValueError("finalist_reserved must be positive")
    return [
        {
            "slot_id": f"finalist-{index:04d}",
            "reported_segment_id": None,
        }
        for index in range(1, finalist_reserved + 1)
    ]


def assignment_diagnostics(
    plan: AssignmentPlan, segment_id: str | None = None
) -> AssignmentDiagnostics:
    """Measure balance and graph gates overall or for one reported segment."""

    if not isinstance(plan, AssignmentPlan):
        raise ValueError("plan must be an AssignmentPlan")
    details = _detailed_diagnostics(plan, segment_id=segment_id)
    return AssignmentDiagnostics(
        connected=details["connected"],
        one_block_resilient=details["one_block_resilient"],
        exposure_range=details["exposure_range"],
        position_range=details["position_range"],
        neighbor_count_min=details["neighbor_count_min"],
        pair_concurrence_max=details["pair_concurrence"]["max"],
        pair_concurrence_variance=details["pair_concurrence"]["variance"],
    )


def _build_segment(
    creative_ids: tuple[str, ...],
    *,
    segment_id: str,
    job_count: int,
    seed: int,
    inclusion_probability: float,
    overall_exposure_counts: dict[str, int],
    overall_position_counts: dict[str, list[int]],
    overall_pair_counts: dict[tuple[str, str], int],
    context_stratum_allocations: tuple[tuple[str, int], ...],
) -> tuple[AssignmentJob, ...]:
    exposure_counts = {creative_id: 0 for creative_id in creative_ids}
    position_counts = {
        creative_id: [0 for _ in range(BLOCK_SIZE)] for creative_id in creative_ids
    }
    pairs = _all_pairs(creative_ids)
    pair_counts = {pair: 0 for pair in pairs}
    context_exposure_counts = {
        context_stratum_id: {
            creative_id: 0 for creative_id in creative_ids
        }
        for context_stratum_id, _ in context_stratum_allocations
    }
    context_position_counts = {
        context_stratum_id: {
            creative_id: [0 for _ in range(BLOCK_SIZE)]
            for creative_id in creative_ids
        }
        for context_stratum_id, _ in context_stratum_allocations
    }
    context_schedule = _schedule_context_strata(
        context_stratum_allocations,
        seed=seed,
        segment_id=segment_id,
    )
    jobs: list[AssignmentJob] = []

    for block_index in range(job_count):
        context_stratum_id = context_schedule[block_index] if context_schedule else None
        exposure_contexts: tuple[Mapping[str, int], ...]
        position_contexts: tuple[
            tuple[Mapping[str, list[int]], int, int, frozenset[tuple[str, int]]],
            ...,
        ]
        if context_stratum_id is None:
            exposure_contexts = (exposure_counts, overall_exposure_counts)
            position_contexts = (
                _position_projection_context(creative_ids, position_counts),
                _position_projection_context(creative_ids, overall_position_counts),
            )
        else:
            exposure_contexts = (
                context_exposure_counts[context_stratum_id],
                exposure_counts,
                overall_exposure_counts,
            )
            position_contexts = (
                _position_projection_context(
                    creative_ids,
                    context_position_counts[context_stratum_id],
                ),
                _position_projection_context(creative_ids, position_counts),
                _position_projection_context(creative_ids, overall_position_counts),
            )
        pair_contexts = (
            _pair_projection_context(pair_counts),
            _pair_projection_context(overall_pair_counts),
        )
        block, order, _ = _choose_assignment_block(
            creative_ids,
            exposure_contexts=exposure_contexts,
            position_contexts=position_contexts,
            pair_contexts=pair_contexts,
            seed=seed,
            segment_id=segment_id,
            block_index=block_index,
        )
        for creative_id in block:
            exposure_counts[creative_id] += 1
            overall_exposure_counts[creative_id] += 1
            if context_stratum_id is not None:
                context_exposure_counts[context_stratum_id][creative_id] += 1
        for position, creative_id in enumerate(order):
            position_counts[creative_id][position] += 1
            overall_position_counts[creative_id][position] += 1
            if context_stratum_id is not None:
                context_position_counts[context_stratum_id][creative_id][position] += 1
        for pair in _block_pairs(block):
            pair_counts[pair] += 1
            overall_pair_counts[pair] += 1
        jobs.append(
            AssignmentJob(
                synthetic_replicate_id=f"{segment_id}-replicate-{block_index + 1:04d}",
                segment_id=segment_id,
                variation_ids=tuple(sorted(block)),
                shown_order=order,
                inclusion_probability=inclusion_probability,
                context_stratum_id=context_stratum_id,
            )
        )

    if not _one_block_resilient(creative_ids, jobs):
        repaired = _repair_segment(creative_ids, jobs)
        if repaired is None:
            raise ValueError(
                f"bounded deterministic repair could not produce a resilient "
                f"comparison graph for segment {segment_id!r}"
            )
        for job in jobs:
            for pair in _block_pairs(job.variation_ids):
                overall_pair_counts[pair] -= 1
        for job in repaired:
            for pair in _block_pairs(job.variation_ids):
                overall_pair_counts[pair] += 1
        jobs = repaired
    jobs = _repair_position_balance(
        creative_ids,
        jobs,
        overall_position_counts=overall_position_counts,
        seed=seed,
        segment_id=segment_id,
    )
    return tuple(jobs)


def _choose_assignment_block(
    creative_ids: tuple[str, ...],
    *,
    exposure_contexts: tuple[Mapping[str, int], ...],
    position_contexts: tuple[
        tuple[Mapping[str, list[int]], int, int, frozenset[tuple[str, int]]],
        ...,
    ],
    pair_contexts: tuple[
        tuple[Mapping[tuple[str, str], int], int, int, int, int], ...
    ],
    seed: int,
    segment_id: str,
    block_index: int,
) -> tuple[tuple[str, ...], tuple[str, ...], _BlockObjective]:
    """Choose the exact lexicographic optimum without seed-filtering candidates.

    Every block with the exposure-optimal composition in the primary balance
    context remains eligible. Candidates are visited in seeded tie order, but
    early termination occurs only after a candidate reaches valid mathematical
    lower bounds for every preceding objective component.
    """

    lower_bounds = (
        tuple(_projected_count_range_lower_bound(context) for context in exposure_contexts),
        tuple(
            _count_range_lower_bound(
                tuple(
                    counts[position]
                    for counts in context[0].values()
                    for position in range(BLOCK_SIZE)
                )
            )
            for context in position_contexts
        ),
        tuple(_projected_pair_maximum_lower_bound(context) for context in pair_contexts),
        tuple(_projected_pair_weight_lower_bound(context) for context in pair_contexts),
    )
    best: tuple[
        tuple[Any, ...], tuple[str, ...], tuple[str, ...], _BlockObjective
    ] | None = None
    for block, block_tie in _candidate_blocks(
        creative_ids,
        exposure_contexts[0],
        seed=seed,
        segment_id=segment_id,
        block_index=block_index,
    ):
        order, projected_position_ranges, order_tie = _best_order(
            block,
            position_contexts=position_contexts,
            seed=seed,
            segment_id=segment_id,
            block_index=block_index,
        )
        objective = _BlockObjective(
            exposure_ranges=tuple(
                _projected_exposure_range(context, block)
                for context in exposure_contexts
            ),
            position_ranges=projected_position_ranges,
            pair_concurrence_maxima=tuple(
                _projected_pair_metrics(context, block)[0]
                for context in pair_contexts
            ),
            pair_concurrence_weights=tuple(
                _projected_pair_metrics(context, block)[1]
                for context in pair_contexts
            ),
            seeded_tie_break=(block_tie, order_tie),
        )
        candidate = (objective.comparison_key(), block, order, objective)
        if best is None or candidate < best:
            best = candidate
        objective_prefix = (
            objective.exposure_ranges,
            objective.position_ranges,
            objective.pair_concurrence_maxima,
            objective.pair_concurrence_weights,
        )
        if objective_prefix == lower_bounds:
            return block, order, objective

    if best is None:  # pragma: no cover - validated inputs always yield candidates
        raise ValueError(f"could not construct assignment block {block_index + 1}")
    return best[1], best[2], best[3]


def _candidate_blocks(
    creative_ids: tuple[str, ...],
    exposure_counts: Mapping[str, int],
    *,
    seed: int,
    segment_id: str,
    block_index: int,
) -> Iterator[tuple[tuple[str, ...], tuple[int, ...]]]:
    """Yield every primary-exposure-optimal block in seeded tie order."""

    minimum = min(exposure_counts.values())
    low = {
        creative_id
        for creative_id in creative_ids
        if exposure_counts[creative_id] == minimum
    }
    low_needed = min(BLOCK_SIZE, len(low))
    seeded_ids = tuple(
        sorted(
            creative_ids,
            key=lambda creative_id: (
                _stable_int(
                    seed,
                    segment_id,
                    block_index,
                    "block-candidate",
                    creative_id,
                ),
                creative_id,
            ),
        )
    )
    for ranks in _rank_combinations_with_low_count(
        seeded_ids,
        low=low,
        low_needed=low_needed,
    ):
        selected = tuple(seeded_ids[rank] for rank in ranks)
        yield tuple(sorted(selected)), ranks


def _rank_combinations_with_low_count(
    seeded_ids: tuple[str, ...],
    *,
    low: set[str],
    low_needed: int,
) -> Iterator[tuple[int, ...]]:
    """Yield eligible rank tuples in true lexicographic order with branch pruning."""

    size = len(seeded_ids)
    low_suffix = [0 for _ in range(size + 1)]
    for index in range(size - 1, -1, -1):
        low_suffix[index] = low_suffix[index + 1] + (seeded_ids[index] in low)

    def visit(
        start: int,
        ranks: tuple[int, ...],
        selected_low: int,
    ) -> Iterator[tuple[int, ...]]:
        slots = BLOCK_SIZE - len(ranks)
        if slots == 0:
            if selected_low == low_needed:
                yield ranks
            return
        for rank in range(start, size - slots + 1):
            next_low = selected_low + (seeded_ids[rank] in low)
            slots_after = slots - 1
            low_required = low_needed - next_low
            if not 0 <= low_required <= slots_after:
                continue
            low_available = low_suffix[rank + 1]
            high_available = size - (rank + 1) - low_available
            if low_required > low_available:
                continue
            if slots_after - low_required > high_available:
                continue
            yield from visit(rank + 1, ranks + (rank,), next_low)

    yield from visit(0, (), 0)


def _best_order(
    block: tuple[str, ...],
    *,
    position_contexts: tuple[
        tuple[Mapping[str, list[int]], int, int, frozenset[tuple[str, int]]], ...
    ],
    seed: int,
    segment_id: str,
    block_index: int,
) -> tuple[tuple[str, ...], tuple[int, ...], int]:
    best: tuple[tuple[int, ...], int, tuple[str, ...]] | None = None
    for order in itertools.permutations(block):
        projected_ranges = tuple(
            _projected_position_range(context, order) for context in position_contexts
        )
        tie = _stable_int(
            seed,
            segment_id,
            block_index,
            "order",
            ",".join(order),
        )
        candidate = (projected_ranges, tie, order)
        if best is None or candidate < best:
            best = candidate
    assert best is not None
    return best[2], best[0], best[1]


def _position_projection_context(
    creative_ids: tuple[str, ...],
    position_counts: Mapping[str, list[int]],
) -> tuple[Mapping[str, list[int]], int, int, frozenset[tuple[str, int]]]:
    current_values = [
        position_counts[creative_id][position]
        for creative_id in creative_ids
        for position in range(BLOCK_SIZE)
    ]
    current_minimum = min(current_values)
    current_maximum = max(current_values)
    minimum_cells = frozenset(
        (creative_id, position)
        for creative_id in creative_ids
        for position in range(BLOCK_SIZE)
        if position_counts[creative_id][position] == current_minimum
    )
    return position_counts, current_minimum, current_maximum, minimum_cells


def _projected_position_range(
    context: tuple[
        Mapping[str, list[int]], int, int, frozenset[tuple[str, int]]
    ],
    order: tuple[str, ...],
) -> int:
    position_counts, current_minimum, current_maximum, minimum_cells = context
    selected_cells = {
        (creative_id, position) for position, creative_id in enumerate(order)
    }
    projected_maximum = max(
        current_maximum,
        max(
            position_counts[creative_id][position] + 1
            for position, creative_id in enumerate(order)
        ),
    )
    projected_minimum = (
        current_minimum + 1 if minimum_cells.issubset(selected_cells) else current_minimum
    )
    return projected_maximum - projected_minimum


def _projected_exposure_range(
    exposure_counts: Mapping[str, int], block: Iterable[str]
) -> int:
    selected = set(block)
    projected = [
        count + (creative_id in selected)
        for creative_id, count in exposure_counts.items()
    ]
    return max(projected) - min(projected)


def _projected_pair_metrics(
    context: tuple[Mapping[tuple[str, str], int], int, int, int, int],
    block: Iterable[str],
) -> tuple[int, int]:
    pair_counts, _pair_total, _current_sum, _current_sum_of_squares, current_maximum = context
    selected_pairs = _block_pairs(block)
    selected_weight = sum(pair_counts[pair] for pair in selected_pairs)
    maximum = max(
        current_maximum,
        max((pair_counts[pair] + 1 for pair in selected_pairs), default=0),
    )
    return maximum, selected_weight


def _pair_projection_context(
    pair_counts: Mapping[tuple[str, str], int],
) -> tuple[Mapping[tuple[str, str], int], int, int, int, int]:
    values = tuple(pair_counts.values())
    return (
        pair_counts,
        len(values),
        sum(values),
        sum(count * count for count in values),
        max(values, default=0),
    )


def _projected_count_range_lower_bound(counts: Mapping[str, int]) -> int:
    return _count_range_lower_bound(tuple(counts.values()))


def _count_range_lower_bound(values: Sequence[int]) -> int:
    current_minimum = min(values)
    current_maximum = max(values)
    minimum_count = sum(value == current_minimum for value in values)
    if current_minimum == current_maximum:
        return 0 if len(values) == BLOCK_SIZE else 1
    if current_maximum - current_minimum == 1:
        return 0 if minimum_count == BLOCK_SIZE else 1
    return max(0, current_maximum - current_minimum - 1)


def _projected_pair_maximum_lower_bound(
    context: tuple[Mapping[tuple[str, str], int], int, int, int, int],
) -> int:
    pair_counts, _pair_total, _current_sum, _current_sum_of_squares, current_maximum = context
    return max(current_maximum, min(pair_counts.values()) + 1)


def _projected_pair_weight_lower_bound(
    context: tuple[Mapping[tuple[str, str], int], int, int, int, int],
) -> int:
    pair_counts = context[0]
    return math.comb(BLOCK_SIZE, 2) * min(pair_counts.values())


def _repair_segment(
    creative_ids: tuple[str, ...], jobs: list[AssignmentJob]
) -> list[AssignmentJob] | None:
    """Try bounded exposure-preserving swaps at matching shown positions."""

    attempts = 0
    for left_index, right_index in itertools.combinations(range(len(jobs)), 2):
        left = jobs[left_index]
        right = jobs[right_index]
        if left.context_stratum_id != right.context_stratum_id:
            continue
        for position in range(BLOCK_SIZE):
            attempts += 1
            if attempts > _REPAIR_SWAP_LIMIT:
                return None
            left_order = list(left.shown_order)
            right_order = list(right.shown_order)
            left_creative = left_order[position]
            right_creative = right_order[position]
            if left_creative == right_creative:
                continue
            if right_creative in left_order or left_creative in right_order:
                continue
            left_order[position], right_order[position] = right_creative, left_creative
            candidate = list(jobs)
            candidate[left_index] = AssignmentJob(
                synthetic_replicate_id=left.synthetic_replicate_id,
                segment_id=left.segment_id,
                variation_ids=tuple(sorted(left_order)),
                shown_order=tuple(left_order),
                inclusion_probability=left.inclusion_probability,
                context_stratum_id=left.context_stratum_id,
            )
            candidate[right_index] = AssignmentJob(
                synthetic_replicate_id=right.synthetic_replicate_id,
                segment_id=right.segment_id,
                variation_ids=tuple(sorted(right_order)),
                shown_order=tuple(right_order),
                inclusion_probability=right.inclusion_probability,
                context_stratum_id=right.context_stratum_id,
            )
            if _one_block_resilient(creative_ids, candidate):
                return candidate
    return None


def _repair_position_balance(
    creative_ids: tuple[str, ...],
    jobs: list[AssignmentJob],
    *,
    overall_position_counts: dict[str, list[int]],
    seed: int,
    segment_id: str,
) -> list[AssignmentJob]:
    """Apply bounded within-block order swaps until position ranges are minimal."""

    segment_counts = {
        creative_id: [0 for _ in range(BLOCK_SIZE)] for creative_id in creative_ids
    }
    for job in jobs:
        for position, creative_id in enumerate(job.shown_order):
            segment_counts[creative_id][position] += 1
    context_counts = {
        context_stratum_id: {
            creative_id: [0 for _ in range(BLOCK_SIZE)]
            for creative_id in creative_ids
        }
        for context_stratum_id in sorted(
            {
                job.context_stratum_id
                for job in jobs
                if job.context_stratum_id is not None
            }
        )
    }
    for job in jobs:
        if job.context_stratum_id is None:
            continue
        for position, creative_id in enumerate(job.shown_order):
            context_counts[job.context_stratum_id][creative_id][position] += 1

    current_objective = _position_balance_objective(
        segment_counts,
        overall_position_counts,
        context_counts,
    )
    repaired = list(jobs)
    visited_states = {tuple(job.shown_order for job in repaired)}
    for step in range(_POSITION_REPAIR_STEP_LIMIT):
        if _position_ranges_are_balanced(
            segment_counts,
            overall_position_counts,
            context_counts,
        ):
            break
        best_improving: tuple[
            tuple[Any, ...], int, int, int, tuple[tuple[str, ...], ...]
        ] | None = None
        best_plateau: tuple[
            tuple[Any, ...], int, int, int, tuple[tuple[str, ...], ...]
        ] | None = None
        for job_index, job in enumerate(repaired):
            for left_position, right_position in itertools.combinations(range(BLOCK_SIZE), 2):
                left_creative = job.shown_order[left_position]
                right_creative = job.shown_order[right_position]
                matrices = [segment_counts, overall_position_counts]
                if job.context_stratum_id is not None:
                    matrices.insert(0, context_counts[job.context_stratum_id])
                _apply_position_swap(
                    matrices,
                    left_creative,
                    right_creative,
                    left_position,
                    right_position,
                )
                objective = _position_balance_objective(
                    segment_counts,
                    overall_position_counts,
                    context_counts,
                )
                _apply_position_swap(
                    matrices,
                    right_creative,
                    left_creative,
                    left_position,
                    right_position,
                )
                tie = _stable_int(
                    seed,
                    segment_id,
                    "position-repair",
                    step,
                    job_index,
                    left_position,
                    right_position,
                )
                candidate_base = (
                    objective,
                    tie,
                    job_index,
                    left_position * BLOCK_SIZE + right_position,
                )
                if objective < current_objective and (
                    best_improving is None or candidate_base < best_improving[:4]
                ):
                    best_improving = candidate_base + ((),)
                elif objective == current_objective:
                    next_order = tuple(
                        right_creative
                        if position == left_position
                        else left_creative
                        if position == right_position
                        else creative_id
                        for position, creative_id in enumerate(job.shown_order)
                    )
                    next_state = tuple(
                        next_order if index == job_index else candidate_job.shown_order
                        for index, candidate_job in enumerate(repaired)
                    )
                    candidate = candidate_base + (next_state,)
                    if next_state not in visited_states and (
                        best_plateau is None or candidate < best_plateau
                    ):
                        best_plateau = candidate
        best = best_improving or best_plateau
        if best is None:
            break
        objective, _, job_index, encoded_positions, next_state = best
        left_position, right_position = divmod(encoded_positions, BLOCK_SIZE)
        job = repaired[job_index]
        order = list(job.shown_order)
        left_creative = order[left_position]
        right_creative = order[right_position]
        _apply_position_swap(
            [
                *(
                    [context_counts[job.context_stratum_id]]
                    if job.context_stratum_id is not None
                    else []
                ),
                segment_counts,
                overall_position_counts,
            ],
            left_creative,
            right_creative,
            left_position,
            right_position,
        )
        order[left_position], order[right_position] = right_creative, left_creative
        repaired[job_index] = AssignmentJob(
            synthetic_replicate_id=job.synthetic_replicate_id,
            segment_id=job.segment_id,
            variation_ids=job.variation_ids,
            shown_order=tuple(order),
            inclusion_probability=job.inclusion_probability,
            context_stratum_id=job.context_stratum_id,
        )
        current_objective = objective
        if next_state:
            visited_states.add(next_state)
    return repaired


def _apply_position_swap(
    count_matrices: Sequence[dict[str, list[int]]],
    left_creative: str,
    right_creative: str,
    left_position: int,
    right_position: int,
) -> None:
    for counts in count_matrices:
        counts[left_creative][left_position] -= 1
        counts[left_creative][right_position] += 1
        counts[right_creative][right_position] -= 1
        counts[right_creative][left_position] += 1


def _position_balance_objective(
    segment_counts: Mapping[str, list[int]],
    overall_counts: Mapping[str, list[int]],
    context_counts: Mapping[str, Mapping[str, list[int]]],
) -> tuple[Any, ...]:
    segment_values = [count for counts in segment_counts.values() for count in counts]
    overall_values = [count for counts in overall_counts.values() for count in counts]
    context_values = {
        context_stratum_id: [
            count for counts in matrix.values() for count in counts
        ]
        for context_stratum_id, matrix in context_counts.items()
    }
    segment_range = max(segment_values) - min(segment_values)
    overall_range = max(overall_values) - min(overall_values)
    context_ranges = tuple(
        max(values) - min(values)
        for values in context_values.values()
    )
    all_ranges = (*context_ranges, segment_range, overall_range)
    return (
        max(all_ranges),
        context_ranges,
        segment_range,
        overall_range,
        tuple(sum(count * count for count in values) for values in context_values.values()),
        sum(count * count for count in segment_values),
        sum(count * count for count in overall_values),
    )


def _position_ranges_are_balanced(
    segment_counts: Mapping[str, list[int]],
    overall_counts: Mapping[str, list[int]],
    context_counts: Mapping[str, Mapping[str, list[int]]],
) -> bool:
    matrices = [*context_counts.values(), segment_counts, overall_counts]
    return all(
        max(values) - min(values) <= 1
        for matrix in matrices
        for values in ([count for counts in matrix.values() for count in counts],)
    )


def _detailed_diagnostics(
    plan: AssignmentPlan, segment_id: str | None
) -> dict[str, Any]:
    known_segments = {known_segment for known_segment, _ in plan.segment_allocations}
    if segment_id is not None and segment_id not in known_segments:
        raise ValueError(f"unknown segment_id: {segment_id}")
    jobs = [job for job in plan.jobs if segment_id is None or job.segment_id == segment_id]
    exposure_counts = {creative_id: 0 for creative_id in plan.creative_ids}
    position_counts = {
        creative_id: {str(position + 1): 0 for position in range(BLOCK_SIZE)}
        for creative_id in plan.creative_ids
    }
    pair_counts = {
        pair: 0 for pair in _all_pairs(plan.creative_ids)
    }
    neighbors = {creative_id: set() for creative_id in plan.creative_ids}

    for job in jobs:
        for creative_id in job.variation_ids:
            exposure_counts[creative_id] += 1
        for position, creative_id in enumerate(job.shown_order, start=1):
            position_counts[creative_id][str(position)] += 1
        for left, right in _block_pairs(job.variation_ids):
            pair_counts[(left, right)] += 1
            neighbors[left].add(right)
            neighbors[right].add(left)

    exposure_values = list(exposure_counts.values())
    position_values = [
        count for creative_counts in position_counts.values() for count in creative_counts.values()
    ]
    pair_values = list(pair_counts.values())
    pair_mean = sum(pair_values) / len(pair_values) if pair_values else 0.0
    pair_variance = (
        sum((count - pair_mean) ** 2 for count in pair_values) / len(pair_values)
        if pair_values
        else 0.0
    )
    neighbor_counts = {
        creative_id: len(creative_neighbors)
        for creative_id, creative_neighbors in neighbors.items()
    }
    return {
        "connected": _is_connected(plan.creative_ids, jobs),
        "one_block_resilient": _one_block_resilient(plan.creative_ids, jobs),
        "exposure_counts": exposure_counts,
        "position_counts": position_counts,
        "neighbor_counts": neighbor_counts,
        "exposure_range": max(exposure_values) - min(exposure_values),
        "position_range": max(position_values) - min(position_values),
        "neighbor_count_min": min(neighbor_counts.values()),
        "pair_concurrence": {
            "counts": [
                {
                    "variation_ids": [left, right],
                    "count": count,
                }
                for (left, right), count in pair_counts.items()
            ],
            "min": min(pair_values, default=0),
            "max": max(pair_values, default=0),
            "range": max(pair_values, default=0) - min(pair_values, default=0),
            "variance": pair_variance,
        },
    }


def _one_block_resilient(
    creative_ids: tuple[str, ...], jobs: Sequence[AssignmentJob]
) -> bool:
    return bool(jobs) and all(
        _is_connected(
            creative_ids,
            jobs[:removed_index] + jobs[removed_index + 1 :],
        )
        for removed_index in range(len(jobs))
    )


def _is_connected(
    creative_ids: tuple[str, ...], jobs: Sequence[AssignmentJob]
) -> bool:
    adjacency = {creative_id: set() for creative_id in creative_ids}
    for job in jobs:
        for left, right in itertools.combinations(job.variation_ids, 2):
            adjacency[left].add(right)
            adjacency[right].add(left)
    visited: set[str] = set()
    pending = [creative_ids[0]]
    while pending:
        creative_id = pending.pop()
        if creative_id in visited:
            continue
        visited.add(creative_id)
        pending.extend(adjacency[creative_id] - visited)
    return len(visited) == len(creative_ids)


def _validate_creative_ids(creative_ids: Sequence[str]) -> tuple[str, ...]:
    if not isinstance(creative_ids, Sequence) or isinstance(creative_ids, (str, bytes)):
        raise ValueError("creative_ids must be a sequence of strings")
    normalized = tuple(creative_ids)
    if len(normalized) < BLOCK_SIZE:
        raise ValueError("creative_ids must contain at least four creatives")
    if len(normalized) > 100:
        raise ValueError("creative_ids must contain at most 100 creatives")
    if not all(isinstance(creative_id, str) and creative_id.strip() for creative_id in normalized):
        raise ValueError("creative_ids must contain only non-empty strings")
    if len(set(normalized)) != len(normalized):
        raise ValueError("creative_ids must be unique")
    return normalized


def _validate_segment_allocations(
    segment_allocations: Mapping[str, int],
) -> tuple[tuple[str, int], ...]:
    if not isinstance(segment_allocations, Mapping) or not segment_allocations:
        raise ValueError("segment_allocations must be a non-empty mapping")
    normalized: list[tuple[str, int]] = []
    for segment_id, job_count in segment_allocations.items():
        if not isinstance(segment_id, str) or not segment_id.strip():
            raise ValueError("segment IDs must be non-empty strings")
        normalized_count = _require_int("segment allocation", job_count)
        if normalized_count < 1:
            raise ValueError("segment allocations must be positive")
        normalized.append((segment_id, normalized_count))
    return tuple(sorted(normalized))


def _validate_context_strata(
    context_strata: Sequence[ContextStratum | Mapping[str, Any]] | None,
    segment_allocations: tuple[tuple[str, int], ...],
) -> tuple[ContextStratum, ...]:
    if context_strata is None:
        return ()
    if not isinstance(context_strata, Sequence) or isinstance(
        context_strata, (str, bytes)
    ):
        raise ValueError("context_strata must be a sequence")
    normalized = tuple(
        stratum
        if isinstance(stratum, ContextStratum)
        else ContextStratum.from_mapping(stratum)
        for stratum in context_strata
    )
    if not normalized:
        return ()
    known_segments = {segment_id for segment_id, _ in segment_allocations}
    supplied_segments = {stratum.segment_id for stratum in normalized}
    unknown_segments = supplied_segments - known_segments
    if unknown_segments:
        raise ValueError(
            "context strata reference unknown segment IDs: "
            + ", ".join(sorted(unknown_segments))
        )
    missing_segments = known_segments - supplied_segments
    if missing_segments:
        raise ValueError(
            "context strata must cover every planned segment; missing: "
            + ", ".join(sorted(missing_segments))
        )
    keys = [
        (stratum.segment_id, stratum.context_stratum_id)
        for stratum in normalized
    ]
    if len(set(keys)) != len(keys):
        raise ValueError("context_stratum_id values must be unique within each segment")
    return tuple(
        sorted(
            normalized,
            key=lambda stratum: (stratum.segment_id, stratum.context_stratum_id),
        )
    )


def _allocate_context_strata(
    context_strata: Sequence[ContextStratum],
    *,
    job_count: int,
    seed: int,
) -> tuple[tuple[str, int], ...]:
    if not context_strata:
        return ()
    total_weight = sum(stratum.planned_weight for stratum in context_strata)
    raw_allocations = [
        job_count * stratum.planned_weight / total_weight
        for stratum in context_strata
    ]
    allocations = [math.floor(raw) for raw in raw_allocations]
    remainder = job_count - sum(allocations)
    remainder_order = sorted(
        range(len(context_strata)),
        key=lambda index: (
            -(raw_allocations[index] - allocations[index]),
            _stable_int(
                seed,
                context_strata[index].segment_id,
                "context-allocation",
                context_strata[index].context_stratum_id,
            ),
            context_strata[index].context_stratum_id,
        ),
    )
    for index in remainder_order[:remainder]:
        allocations[index] += 1
    return tuple(
        (stratum.context_stratum_id, allocations[index])
        for index, stratum in enumerate(context_strata)
    )


def _schedule_context_strata(
    allocations: tuple[tuple[str, int], ...],
    *,
    seed: int,
    segment_id: str,
) -> tuple[str, ...]:
    if not allocations:
        return ()
    targets = dict(allocations)
    assigned = {context_stratum_id: 0 for context_stratum_id, _ in allocations}
    schedule: list[str] = []
    for slot in range(sum(targets.values())):
        available = [
            context_stratum_id
            for context_stratum_id, target in targets.items()
            if assigned[context_stratum_id] < target
        ]
        selected = min(
            available,
            key=lambda context_stratum_id: (
                Fraction(
                    assigned[context_stratum_id],
                    targets[context_stratum_id],
                ),
                _stable_int(
                    seed,
                    segment_id,
                    "context-schedule",
                    slot,
                    context_stratum_id,
                ),
                context_stratum_id,
            ),
        )
        assigned[selected] += 1
        schedule.append(selected)
    return tuple(schedule)


def plan_context_stratum_schedule(
    context_strata: Sequence[ContextStratum | Mapping[str, Any]],
    *,
    segment_id: str,
    job_count: int,
    seed: int,
) -> tuple[
    tuple[ContextStratum, ...],
    tuple[tuple[str, int], ...],
    tuple[str, ...],
]:
    """Return one validated weighted context schedule for a named segment."""

    if not isinstance(segment_id, str) or not segment_id.strip():
        raise ValueError("segment_id must be a non-empty string")
    job_count = _require_int("job_count", job_count)
    seed = _require_int("seed", seed)
    if job_count < 1:
        raise ValueError("job_count must be positive")
    normalized = tuple(
        stratum
        if isinstance(stratum, ContextStratum)
        else ContextStratum.from_mapping(stratum)
        for stratum in context_strata
    )
    if any(stratum.segment_id != segment_id for stratum in normalized):
        raise ValueError("context strata must belong to the scheduled segment")
    identifiers = [stratum.context_stratum_id for stratum in normalized]
    if len(set(identifiers)) != len(identifiers):
        raise ValueError("context_stratum_id values must be unique within each segment")
    normalized = tuple(sorted(normalized, key=lambda item: item.context_stratum_id))
    allocations = _allocate_context_strata(
        normalized,
        job_count=job_count,
        seed=seed,
    )
    schedule = _schedule_context_strata(
        allocations,
        seed=seed,
        segment_id=segment_id,
    )
    return normalized, allocations, schedule


def _context_stratum_diagnostics(
    plan: AssignmentPlan,
    *,
    segment_id: str,
    context_stratum_id: str,
    planned_jobs: int,
) -> dict[str, Any]:
    jobs = [
        job
        for job in plan.jobs
        if job.segment_id == segment_id
        and job.context_stratum_id == context_stratum_id
    ]
    exposure_counts = {creative_id: 0 for creative_id in plan.creative_ids}
    position_counts = {
        creative_id: {str(position + 1): 0 for position in range(BLOCK_SIZE)}
        for creative_id in plan.creative_ids
    }
    for job in jobs:
        for creative_id in job.variation_ids:
            exposure_counts[creative_id] += 1
        for position, creative_id in enumerate(job.shown_order, start=1):
            position_counts[creative_id][str(position)] += 1
    exposure_values = tuple(exposure_counts.values())
    position_values = tuple(
        count
        for creative_counts in position_counts.values()
        for count in creative_counts.values()
    )
    return {
        "diagnostic_scope": "planned_assignment_balance",
        "segment_id": segment_id,
        "context_stratum_id": context_stratum_id,
        "planned_jobs": planned_jobs,
        "assigned_jobs": len(jobs),
        "exposure_counts": exposure_counts,
        "position_counts": position_counts,
        "exposure_range": max(exposure_values) - min(exposure_values),
        "position_range": max(position_values) - min(position_values),
    }


def _require_int(name: str, value: Any) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValueError(f"{name} must be an integer")
    return value


def _canonical_pair(left: str, right: str) -> tuple[str, str]:
    return (left, right) if left < right else (right, left)


def _all_pairs(creative_ids: Iterable[str]) -> tuple[tuple[str, str], ...]:
    return tuple(itertools.combinations(sorted(creative_ids), 2))


def _block_pairs(block: Iterable[str]) -> tuple[tuple[str, str], ...]:
    return tuple(
        _canonical_pair(left, right)
        for left, right in itertools.combinations(sorted(block), 2)
    )


def _stable_int(*parts: object) -> int:
    encoded = "\x1f".join(str(part) for part in parts).encode("utf-8")
    return int.from_bytes(hashlib.sha256(encoded).digest()[:8], "big")


__all__ = [
    "AssignmentDiagnostics",
    "AssignmentJob",
    "AssignmentPlan",
    "assignment_diagnostics",
    "build_assignments",
    "build_boundary_reserve_slots",
    "build_finalist_reserve_slots",
    "plan_context_stratum_schedule",
]
