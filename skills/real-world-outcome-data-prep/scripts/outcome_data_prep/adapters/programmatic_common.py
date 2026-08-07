"""Shared closed provenance authentication for programmatic adapters."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import asdict

from ..capabilities import AdapterCapability
from ..common import sha256_json
from .base import AdapterError


def require_programmatic_capability(
    capability: AdapterCapability,
    expected_sha256: Mapping[str, str],
    adapter_name: str,
) -> None:
    if type(capability) is not AdapterCapability:
        raise AdapterError(f"{adapter_name} capability is invalid")
    expected = expected_sha256.get(capability.adapter_id)
    if expected is None or sha256_json(asdict(capability)) != expected:
        raise AdapterError(
            f"{adapter_name} requires an authenticated exact capability record"
        )
