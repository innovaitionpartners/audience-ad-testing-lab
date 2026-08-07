"""Hidden grading contracts for synthetic persona-behavior studies."""

from .contracts import (
    EVALUATION_VERSION,
    ORACLE_VERSION,
    validate_oracle,
    validate_synthetic_evaluation,
)

__all__ = [
    "EVALUATION_VERSION",
    "ORACLE_VERSION",
    "validate_oracle",
    "validate_synthetic_evaluation",
]
