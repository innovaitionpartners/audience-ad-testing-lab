"""Sufficient-statistic adapters for Tier 4 held-out outcome evidence.

The adapters deliberately accept only a sealed validation observation.  They
do not infer a denominator, reinterpret a total, or manufacture precision
from individual-level data: the aggregate already authenticated by the
observation is the entire statistical input.
"""

from __future__ import annotations

from dataclasses import dataclass
import math
from collections.abc import Mapping
from statistics import NormalDist
import weakref

from ...common import ContractError
from .contracts import project_shared_outcome_evidence, validate_validation_observation


METRIC_FAMILIES = frozenset({"binary_proportion", "continuous_mean", "event_rate"})


@dataclass(frozen=True)
class _MetricCalculationContext:
    """Authenticated immutable inputs required for a pairwise calculation."""

    registration_sha256: str
    study_id: str
    metric_family: str
    direction: str
    metric_fingerprint: tuple[str, ...]
    success_count: int | None = None
    eligible_exposure_count: int | None = None
    sample_count: int | None = None
    standard_deviation: float | None = None
    event_count: int | None = None
    exposure_time: float | None = None


@dataclass(frozen=True)
class NormalizedArm:
    arm_id: str
    block_id: str
    creative_id: str
    point: float
    direction_normalized_point: float
    effective_sample: float
    lower: float | None
    upper: float | None
    interval_method: str
    support_status: str
    limitation_codes: tuple[str, ...]


@dataclass(frozen=True)
class DifferenceInterval:
    point: float
    lower: float | None
    upper: float | None
    confidence_level: float | None
    method: str


# Context is deliberately kept out of the public dataclass payload. The map is
# identity-bound (rather than equality-bound) so copying or reconstructing a
# visible arm cannot inherit authenticated calculation authority.
_NORMALIZED_CONTEXTS: dict[
    int, tuple[weakref.ReferenceType[NormalizedArm], _MetricCalculationContext]
] = {}


def _register_context(
    arm: NormalizedArm, context: _MetricCalculationContext,
) -> NormalizedArm:
    arm_identity = id(arm)

    def _cleanup(reference: weakref.ReferenceType[NormalizedArm]) -> None:
        stored = _NORMALIZED_CONTEXTS.get(arm_identity)
        if stored is not None and stored[0] is reference:
            _NORMALIZED_CONTEXTS.pop(arm_identity, None)

    _NORMALIZED_CONTEXTS[arm_identity] = (weakref.ref(arm, _cleanup), context)
    return arm


def _finite_number(value: object, path: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ContractError(f"{path} must be a finite number")
    return float(value)


def _nonnegative_integer(value: object, path: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ContractError(f"{path} must be a non-negative integer")
    return value


def _positive_integer(value: object, path: str) -> int:
    result = _nonnegative_integer(value, path)
    if result == 0:
        raise ContractError(f"{path} must be a positive integer")
    return result


def _normal_quantile(probability: float) -> float:
    """Return the deterministic standard-library inverse normal CDF."""
    if not 0.0 < probability < 1.0:
        raise ContractError("normal probability must be between zero and one")
    return NormalDist().inv_cdf(probability)


def wilson_score_interval(successes: int, exposures: int, *, confidence_level: float) -> tuple[float, float]:
    """Return a two-sided Wilson score interval without a normal shortcut."""
    if not 0.0 < confidence_level < 1.0:
        raise ContractError("confidence_level must be between zero and one")
    if successes < 0 or exposures <= 0 or successes > exposures:
        raise ContractError("Wilson interval requires 0 <= successes <= positive exposures")
    z = _normal_quantile(0.5 + confidence_level / 2.0)
    n = float(exposures)
    point = successes / n
    denominator = 1.0 + z * z / n
    centre = (point + z * z / (2.0 * n)) / denominator
    spread = z * math.sqrt(point * (1.0 - point) / n + z * z / (4.0 * n * n)) / denominator
    return max(0.0, centre - spread), min(1.0, centre + spread)


def _binary_proportion(aggregate: Mapping[str, object]) -> tuple[float, float, float, float, str]:
    try:
        successes = _nonnegative_integer(aggregate["success_count"], "aggregate.success_count")
        exposures = _positive_integer(aggregate["eligible_exposure_count"], "aggregate.eligible_exposure_count")
    except KeyError as exc:
        raise ContractError("binary_proportion aggregate requires success_count and eligible_exposure_count") from exc
    if successes > exposures:
        raise ContractError("success_count cannot exceed eligible_exposure_count")
    point = successes / exposures
    lower, upper = wilson_score_interval(successes, exposures, confidence_level=0.975)
    return point, lower, upper, float(exposures), "wilson-score"


def _regularized_gamma_p(shape: float, value: float) -> float:
    """Regularized lower incomplete gamma P(shape, value), stdlib only."""
    if shape <= 0.0 or value < 0.0:
        raise ContractError("incomplete gamma arguments are invalid")
    if value == 0.0:
        return 0.0
    log_factor = -value + shape * math.log(value) - math.lgamma(shape)
    if value < shape + 1.0:
        term = 1.0 / shape
        total = term
        for index in range(1, 10_001):
            term *= value / (shape + index)
            total += term
            if abs(term) <= abs(total) * 2.0e-15:
                return min(1.0, total * math.exp(log_factor))
        raise ContractError("incomplete gamma series did not converge")
    # Continued fraction for Q, then use P = 1 - Q.  The tiny floor avoids
    # a division by zero while remaining well below useful double precision.
    tiny = 1.0e-300
    b = value + 1.0 - shape
    c = 1.0 / tiny
    d = 1.0 / max(b, tiny)
    fraction = d
    for index in range(1, 10_001):
        coefficient = -index * (index - shape)
        b += 2.0
        d = coefficient * d + b
        if abs(d) < tiny:
            d = tiny
        c = b + coefficient / c
        if abs(c) < tiny:
            c = tiny
        d = 1.0 / d
        delta = d * c
        fraction *= delta
        if abs(delta - 1.0) <= 2.0e-15:
            q = math.exp(log_factor) * fraction
            return max(0.0, min(1.0, 1.0 - q))
    raise ContractError("incomplete gamma fraction did not converge")


def _chi_square_quantile(probability: float, degrees_of_freedom: float) -> float:
    if not 0.0 < probability < 1.0 or degrees_of_freedom <= 0.0:
        raise ContractError("chi-square quantile arguments are invalid")
    shape = degrees_of_freedom / 2.0
    lower, upper = 0.0, max(1.0, degrees_of_freedom)
    while _regularized_gamma_p(shape, upper / 2.0) < probability:
        upper *= 2.0
        if upper > 1.0e308 / 2.0:
            raise ContractError("chi-square quantile is not finite")
    for _ in range(100):
        middle = (lower + upper) / 2.0
        if _regularized_gamma_p(shape, middle / 2.0) < probability:
            lower = middle
        else:
            upper = middle
    return (lower + upper) / 2.0


def garwood_interval(events: int, exposure_time: float, *, confidence_level: float) -> tuple[float, float]:
    """Return exact Poisson/Garwood rate limits for exposure_time."""
    if not 0.0 < confidence_level < 1.0:
        raise ContractError("confidence_level must be between zero and one")
    if events < 0 or not math.isfinite(exposure_time) or exposure_time <= 0.0:
        raise ContractError("Garwood interval requires non-negative events and positive exposure_time")
    alpha = 1.0 - confidence_level
    lower = 0.0 if events == 0 else 0.5 * _chi_square_quantile(alpha / 2.0, 2.0 * events)
    upper = 0.5 * _chi_square_quantile(1.0 - alpha / 2.0, 2.0 * (events + 1))
    return lower / exposure_time, upper / exposure_time


def _event_rate(aggregate: Mapping[str, object]) -> tuple[float, float, float, float, str]:
    try:
        events = _nonnegative_integer(aggregate["event_count"], "aggregate.event_count")
        exposure_time = _finite_number(aggregate["exposure_time"], "aggregate.exposure_time")
    except KeyError as exc:
        raise ContractError("event_rate aggregate requires event_count and exposure_time") from exc
    if exposure_time <= 0.0:
        raise ContractError("exposure_time must be positive")
    point = events / exposure_time
    lower, upper = garwood_interval(events, exposure_time, confidence_level=0.975)
    return point, lower, upper, exposure_time, "garwood-poisson"


def _continuous_mean(aggregate: Mapping[str, object]) -> tuple[float, float | None, float | None, float, str, tuple[str, ...]]:
    try:
        count = _positive_integer(aggregate["sample_count"], "aggregate.sample_count")
        mean = _finite_number(aggregate["mean"], "aggregate.mean")
        standard_deviation = _finite_number(aggregate["standard_deviation"], "aggregate.standard_deviation")
    except KeyError as exc:
        raise ContractError("continuous_mean aggregate requires sample_count, mean, and standard_deviation") from exc
    if standard_deviation < 0.0:
        raise ContractError("standard_deviation must be non-negative")
    if count == 1:
        # A standard deviation of zero with one observation is not evidence of
        # no uncertainty.  Preserve the observed value but mark it unusable in
        # any directional comparison.
        return mean, None, None, 1.0, "student-t-unavailable", ("continuous-sample-too-small",)
    standard_error = standard_deviation / math.sqrt(count)
    critical = _student_t_quantile(0.9875, count - 1)
    return mean, mean - critical * standard_error, mean + critical * standard_error, float(count), "student-t", ()


def _regularized_beta(value: float, left: float, right: float) -> float:
    if value <= 0.0:
        return 0.0
    if value >= 1.0:
        return 1.0
    log_front = left * math.log(value) + right * math.log1p(-value) - math.lgamma(left) - math.lgamma(right) + math.lgamma(left + right)

    def fraction(a: float, b: float, x: float) -> float:
        tiny = 1.0e-300
        qab, qap, qam = a + b, a + 1.0, a - 1.0
        c = 1.0
        d = 1.0 - qab * x / qap
        d = 1.0 / max(d, tiny)
        result = d
        for step in range(1, 10_001):
            m2 = 2 * step
            aa = step * (b - step) * x / ((qam + m2) * (a + m2))
            d = 1.0 + aa * d
            if abs(d) < tiny: d = tiny
            c = 1.0 + aa / c
            if abs(c) < tiny: c = tiny
            d = 1.0 / d
            result *= d * c
            aa = -(a + step) * (qab + step) * x / ((a + m2) * (qap + m2))
            d = 1.0 + aa * d
            if abs(d) < tiny: d = tiny
            c = 1.0 + aa / c
            if abs(c) < tiny: c = tiny
            d = 1.0 / d
            delta = d * c
            result *= delta
            if abs(delta - 1.0) <= 2.0e-15:
                return result
        raise ContractError("incomplete beta fraction did not converge")

    if value < (left + 1.0) / (left + right + 2.0):
        return math.exp(log_front) * fraction(left, right, value) / left
    return 1.0 - math.exp(log_front) * fraction(right, left, 1.0 - value) / right


def _student_t_cdf(value: float, degrees_of_freedom: float) -> float:
    if degrees_of_freedom <= 0.0 or not math.isfinite(degrees_of_freedom):
        raise ContractError("Student-t degrees of freedom must be positive and finite")
    if value == 0.0:
        return 0.5
    x = degrees_of_freedom / (degrees_of_freedom + value * value)
    tail = 0.5 * _regularized_beta(x, degrees_of_freedom / 2.0, 0.5)
    return 1.0 - tail if value > 0.0 else tail


def _student_t_quantile(probability: float, degrees_of_freedom: float) -> float:
    if not 0.0 < probability < 1.0:
        raise ContractError("Student-t probability must be between zero and one")
    if probability == 0.5:
        return 0.0
    if probability < 0.5:
        return -_student_t_quantile(1.0 - probability, degrees_of_freedom)
    lower, upper = 0.0, max(1.0, _normal_quantile(probability))
    while _student_t_cdf(upper, degrees_of_freedom) < probability:
        upper *= 2.0
    for _ in range(100):
        middle = (lower + upper) / 2.0
        if _student_t_cdf(middle, degrees_of_freedom) < probability:
            lower = middle
        else:
            upper = middle
    return (lower + upper) / 2.0


def _metric_matches_registration(observation: Mapping[str, object]) -> None:
    metric = observation["metric"]
    units = observation["units"]
    windows = observation["windows"]
    assert isinstance(metric, Mapping) and isinstance(units, Mapping) and isinstance(windows, Mapping)
    if units["exposure"] != metric["exposure_unit"]:
        raise ContractError("observation exposure unit must match metric.exposure_unit")
    if units["outcome"] != metric["outcome_unit"]:
        raise ContractError("observation outcome unit must match metric.outcome_unit")
    if windows["measurement"] != metric["measurement_window"]:
        raise ContractError("observation measurement window must match metric.measurement_window")
    if windows["attribution"] != metric["attribution_window"]:
        raise ContractError("observation attribution window must match metric.attribution_window")
    registration = observation["registration_binding"]
    assert isinstance(registration, Mapping)
    preregistration = registration["preregistration"]
    assert isinstance(preregistration, Mapping)
    registered_metrics = [preregistration["primary_metric"], *preregistration["secondary_metrics"]]
    if metric not in registered_metrics:
        raise ContractError("observation metric (including direction) must match a sealed preregistration metric")


def _require_adapter_aggregate_fields(observation: Mapping[str, object]) -> None:
    """Give insufficient statistics one clear, family-specific failure."""
    family = observation.get("metric_family")
    aggregate = observation.get("aggregate")
    required = {
        "binary_proportion": ("success_count", "eligible_exposure_count"),
        "continuous_mean": ("sample_count", "mean", "standard_deviation"),
        "event_rate": ("event_count", "exposure_time"),
    }.get(family)
    if required is not None and (not isinstance(aggregate, Mapping) or any(key not in aggregate for key in required)):
        raise ContractError(f"{family} aggregate requires {', '.join(required)}")


def normalize_observation(observation: dict[str, object]) -> NormalizedArm:
    """Authenticate and normalize one canonical observation into one arm."""
    _require_adapter_aggregate_fields(observation)
    validated = validate_validation_observation(observation)
    projected = project_shared_outcome_evidence(validated)
    binding = validated["shared_outcome_evidence_binding"]
    assert isinstance(binding, Mapping)
    if projected["shared_evidence_sha256"] != binding["shared_evidence_sha256"]:
        raise ContractError("observation repeated outcome fields do not reproduce shared outcome evidence")
    _metric_matches_registration(validated)
    family = validated["metric_family"]
    if family not in METRIC_FAMILIES:
        raise ContractError("metric_family is unsupported")
    aggregate = validated["aggregate"]
    metric = validated["metric"]
    creative = validated["creative_binding"]
    assert isinstance(aggregate, Mapping) and isinstance(metric, Mapping) and isinstance(creative, Mapping)
    limitations: tuple[str, ...] = ()
    if family == "binary_proportion":
        point, lower, upper, effective_sample, method = _binary_proportion(aggregate)
    elif family == "continuous_mean":
        point, lower, upper, effective_sample, method, limitations = _continuous_mean(aggregate)
    else:
        point, lower, upper, effective_sample, method = _event_rate(aggregate)
    direction = metric["direction"]
    assert isinstance(direction, str)
    multiplier = 1.0 if direction == "higher_is_better" else -1.0
    registration = validated["registration_binding"]
    assert isinstance(registration, Mapping)
    context_values: dict[str, object] = {}
    if family == "binary_proportion":
        context_values = {
            "success_count": int(aggregate["success_count"]),
            "eligible_exposure_count": int(aggregate["eligible_exposure_count"]),
        }
    elif family == "continuous_mean":
        context_values = {
            "sample_count": int(aggregate["sample_count"]),
            "standard_deviation": float(aggregate["standard_deviation"]),
        }
    else:
        context_values = {
            "event_count": int(aggregate["event_count"]),
            "exposure_time": float(aggregate["exposure_time"]),
        }
    context = _MetricCalculationContext(
        registration_sha256=str(registration["registration_sha256"]),
        study_id=str(binding["study_id"]), metric_family=str(family),
        direction=direction,
        metric_fingerprint=(
            str(metric["name"]), str(metric["definition"]), str(metric["direction"]),
            str(metric["exposure_unit"]), str(metric["outcome_unit"]),
            str(metric["measurement_window"]), str(metric["attribution_window"]),
        ),
        **context_values,
    )
    arm = NormalizedArm(
        arm_id=str(validated["arm_id"]), block_id=str(validated["block_id"]),
        creative_id=str(creative["creative_id"]), point=point,
        direction_normalized_point=multiplier * point,
        effective_sample=effective_sample, lower=lower, upper=upper,
        interval_method=method,
        support_status="limited" if limitations else "supported",
        limitation_codes=limitations,
    )
    return _register_context(arm, context)


def _context(arm: NormalizedArm) -> _MetricCalculationContext:
    stored = _NORMALIZED_CONTEXTS.get(id(arm))
    if stored is None or stored[0]() is not arm:
        raise ContractError("classify_observed_pair requires arms from normalize_observation")
    return stored[1]


def _normalized_interval(lower: float, upper: float, direction: str) -> tuple[float, float]:
    return (lower, upper) if direction == "higher_is_better" else (-upper, -lower)


def _bonferroni_difference_interval(left: NormalizedArm, right: NormalizedArm, confidence_level: float) -> DifferenceInterval:
    left_context, right_context = _context(left), _context(right)
    family, direction = left_context.metric_family, left_context.direction
    arm_confidence = 1.0 - (1.0 - confidence_level) / 2.0
    if family == "binary_proportion":
        assert left_context.success_count is not None and left_context.eligible_exposure_count is not None
        assert right_context.success_count is not None and right_context.eligible_exposure_count is not None
        left_bounds = wilson_score_interval(left_context.success_count, left_context.eligible_exposure_count, confidence_level=arm_confidence)
        right_bounds = wilson_score_interval(right_context.success_count, right_context.eligible_exposure_count, confidence_level=arm_confidence)
    else:
        assert left_context.event_count is not None and left_context.exposure_time is not None
        assert right_context.event_count is not None and right_context.exposure_time is not None
        left_bounds = garwood_interval(left_context.event_count, left_context.exposure_time, confidence_level=arm_confidence)
        right_bounds = garwood_interval(right_context.event_count, right_context.exposure_time, confidence_level=arm_confidence)
    left_lower, left_upper = _normalized_interval(*left_bounds, direction)
    right_lower, right_upper = _normalized_interval(*right_bounds, direction)
    method = "bonferroni-wilson" if family == "binary_proportion" else "bonferroni-garwood"
    return DifferenceInterval(
        point=left.direction_normalized_point - right.direction_normalized_point,
        lower=left_lower - right_upper, upper=left_upper - right_lower,
        confidence_level=confidence_level, method=method,
    )


def _welch_components(
    left: NormalizedArm,
    right: NormalizedArm,
    confidence_level: float,
) -> tuple[float, float, float, float, float] | None:
    """Return private Welch point, bounds, variance sum, and degrees of freedom."""
    left_context, right_context = _context(left), _context(right)
    direction = left_context.direction
    assert left_context.sample_count is not None and left_context.standard_deviation is not None
    assert right_context.sample_count is not None and right_context.standard_deviation is not None
    left_count, right_count = left_context.sample_count, right_context.sample_count
    left_sd, right_sd = left_context.standard_deviation, right_context.standard_deviation
    point = left.direction_normalized_point - right.direction_normalized_point
    if left_count < 2 or right_count < 2 or (left_sd == 0.0 and right_sd == 0.0):
        return None
    left_variance_term = left_sd * left_sd / left_count
    right_variance_term = right_sd * right_sd / right_count
    standard_error = math.sqrt(left_variance_term + right_variance_term)
    denominator = 0.0
    if left_variance_term:
        denominator += left_variance_term * left_variance_term / (left_count - 1)
    if right_variance_term:
        denominator += right_variance_term * right_variance_term / (right_count - 1)
    if standard_error == 0.0 or denominator == 0.0:
        return None
    degrees_of_freedom = (left_variance_term + right_variance_term) ** 2 / denominator
    critical = _student_t_quantile(0.5 + confidence_level / 2.0, degrees_of_freedom)
    raw_point = left.point - right.point
    raw_lower, raw_upper = raw_point - critical * standard_error, raw_point + critical * standard_error
    lower, upper = _normalized_interval(raw_lower, raw_upper, direction)
    return point, lower, upper, left_variance_term + right_variance_term, degrees_of_freedom


def _welch_difference_interval(
    left: NormalizedArm, right: NormalizedArm, confidence_level: float,
) -> tuple[DifferenceInterval, bool]:
    components = _welch_components(left, right, confidence_level)
    point = left.direction_normalized_point - right.direction_normalized_point
    if components is None:
        return DifferenceInterval(point, None, None, None, "welch-student-t-unavailable"), True
    _, lower, upper, _, _ = components
    return DifferenceInterval(point, lower, upper, confidence_level, "welch-student-t"), False


def classify_observed_pair(left: NormalizedArm, right: NormalizedArm, *, equivalence_margin: float, confidence_level: float = 0.95) -> tuple[str, DifferenceInterval]:
    """Classify left minus right using the metric family's reviewed interval."""
    margin = _finite_number(equivalence_margin, "equivalence_margin")
    level = _finite_number(confidence_level, "confidence_level")
    if margin < 0.0:
        raise ContractError("equivalence_margin must be non-negative")
    if not 0.0 < level < 1.0:
        raise ContractError("confidence_level must be between zero and one")
    left_context, right_context = _context(left), _context(right)
    if left.arm_id == right.arm_id:
        raise ContractError("cannot compare an arm with itself; pair arms must be distinct")
    if (
        left.block_id != right.block_id
        or left_context.study_id != right_context.study_id
        or left_context.registration_sha256 != right_context.registration_sha256
    ):
        raise ContractError("cannot compare observations outside the same registered block/study context")
    family = left_context.metric_family
    if family != right_context.metric_family:
        raise ContractError("cannot compare different metric families")
    if left_context.metric_fingerprint != right_context.metric_fingerprint:
        raise ContractError("cannot compare observations with mismatched metric direction, units, or windows")
    if left.support_status != "supported" or right.support_status != "supported":
        return "observed_indeterminate", DifferenceInterval(
            left.direction_normalized_point - right.direction_normalized_point,
            None, None, None, "unavailable-limited",
        )
    if family == "continuous_mean":
        interval, degenerate = _welch_difference_interval(left, right, level)
    elif family in {"binary_proportion", "event_rate"}:
        interval, degenerate = _bonferroni_difference_interval(left, right, level), False
    else:  # Defensive: manually-created NormalizedArm instances cannot reach here.
        raise ContractError("metric_family is unsupported")
    if degenerate:
        return "observed_indeterminate", interval
    assert interval.lower is not None and interval.upper is not None
    if interval.lower > margin:
        return "observed_a_above_b", interval
    if interval.upper < -margin:
        return "observed_b_above_a", interval
    if interval.lower >= -margin and interval.upper <= margin:
        return "observed_equivalent", interval
    return "observed_indeterminate", interval
