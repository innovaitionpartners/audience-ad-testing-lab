"""Registry-driven population source planning and canonical adapters."""

from .adapters.base import PopulationAdapter
from .registry import (
    SOURCE_REGISTRY_VERSION,
    load_population_adapter,
    route_population_sources,
    validate_source_registry,
)
from .validation.contracts import (
    validate_claim_family,
    validate_comparison,
    validate_held_out_evaluation,
    validate_preregistration,
    validate_shared_outcome_evidence,
    validate_tier4_claim,
    validate_validation_observation,
)

__all__ = [
    "PopulationAdapter",
    "SOURCE_REGISTRY_VERSION",
    "load_population_adapter",
    "route_population_sources",
    "validate_source_registry",
    "validate_claim_family",
    "validate_comparison",
    "validate_held_out_evaluation",
    "validate_preregistration",
    "validate_shared_outcome_evidence",
    "validate_tier4_claim",
    "validate_validation_observation",
]
