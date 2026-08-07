"""Closed interfaces shared by real-world outcome export adapters."""

from .base import (
    AdapterError,
    AdapterInventory,
    AdapterResult,
    AdapterValidation,
    ExactVariantAdapter,
    OutcomeAdapter,
)

__all__ = [
    "AdapterError",
    "AdapterInventory",
    "AdapterResult",
    "AdapterValidation",
    "ExactVariantAdapter",
    "OutcomeAdapter",
]
