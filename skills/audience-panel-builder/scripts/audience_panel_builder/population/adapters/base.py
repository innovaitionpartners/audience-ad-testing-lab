"""Shared protocol and deterministic helpers for population adapters."""

from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import sys
from typing import Protocol, runtime_checkable
from urllib.parse import urlparse

from ...common import ContractError, canonical_json_bytes, sha256_json


SKILLS_ROOT = Path(__file__).resolve().parents[5]
RESEARCH_SCRIPTS = SKILLS_ROOT / "audience-ad-testing-lab" / "scripts"
if str(RESEARCH_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(RESEARCH_SCRIPTS))

from audience_lab.audience_research_v3 import (  # noqa: E402
    validate_observation_batch,
)


OBSERVATION_BATCH_VERSION = "audience-frame-observation-batch-v1"


@runtime_checkable
class PopulationAdapter(Protocol):
    def descriptor(self) -> dict[str, object]: ...

    def plan(
        self,
        frame_request: dict[str, object],
        capabilities: dict[str, object],
    ) -> dict[str, object]: ...

    def acquire(
        self,
        dataset_plan: dict[str, object],
        destination: Path,
    ) -> dict[str, object]: ...

    def normalize(
        self,
        raw_snapshot: dict[str, object],
        mapping: dict[str, object] | None = None,
    ) -> dict[str, object]: ...


class PinnedSnapshotAdapter:
    """Base for adapters whose conformance lane reads committed snapshots."""

    DESCRIPTOR: dict[str, object]

    def descriptor(self) -> dict[str, object]:
        return deepcopy(self.DESCRIPTOR)

    def plan(
        self,
        frame_request: dict[str, object],
        capabilities: dict[str, object],
    ) -> dict[str, object]:
        return {
            "adapter_id": self.DESCRIPTOR["adapter_id"],
            "frame_request_id": frame_request.get("request_id"),
            "capabilities": deepcopy(capabilities),
            "network_acquisition": False,
        }

    def acquire(
        self,
        dataset_plan: dict[str, object],
        destination: Path,
    ) -> dict[str, object]:
        if dataset_plan.get("network_acquisition") is not False:
            raise ContractError(
                "network acquisition requires an explicit integration route"
            )
        path_text = dataset_plan.get("snapshot_path")
        if not isinstance(path_text, str) or not path_text:
            raise ContractError("dataset plan snapshot_path must be a local file")
        if urlparse(path_text).scheme:
            raise ContractError(
                "network acquisition requires an explicit integration route"
            )
        expected_hash = dataset_plan.get("snapshot_sha256")
        if not isinstance(expected_hash, str):
            raise ContractError("dataset plan snapshot_sha256 is required")
        source = Path(path_text)
        try:
            payload = json.loads(source.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ContractError("pinned population snapshot is unreadable") from exc
        if not isinstance(payload, dict):
            raise ContractError("pinned population snapshot must be an object")
        if sha256_json(payload) != expected_hash:
            raise ContractError("pinned population snapshot hash mismatch")
        destination.parent.mkdir(parents=True, exist_ok=True)
        try:
            with destination.open("xb") as handle:
                handle.write(canonical_json_bytes(payload))
        except FileExistsError as exc:
            raise ContractError(
                f"population snapshot destination already exists: {destination}"
            ) from exc
        return payload


def require_mapping(
    mapping: dict[str, object] | None,
    keys: set[str],
) -> dict[str, object]:
    if not isinstance(mapping, dict):
        raise ContractError("adapter mapping must be an object")
    unknown = sorted(set(mapping) - keys)
    missing = sorted(keys - set(mapping))
    if unknown:
        raise ContractError(
            "adapter mapping has unknown fields: " + ", ".join(unknown)
        )
    if missing:
        raise ContractError(
            "adapter mapping is missing fields: " + ", ".join(missing)
        )
    return mapping


def require_snapshot(
    value: object,
    *,
    keys: set[str],
    unit: str,
) -> dict[str, object]:
    if not isinstance(value, dict):
        raise ContractError("population snapshot must be an object")
    unknown = sorted(set(value) - keys)
    missing = sorted(keys - set(value))
    if unknown:
        raise ContractError(
            "population snapshot has unknown fields: " + ", ".join(unknown)
        )
    if missing:
        raise ContractError(
            "population snapshot is missing fields: " + ", ".join(missing)
        )
    if value["snapshot_version"] != "population-public-proxy-snapshot-v1":
        raise ContractError("population snapshot version is unsupported")
    if value["unit"] != unit:
        raise ContractError(
            f"population snapshot unit must be {unit}; got {value['unit']}"
        )
    if not isinstance(value["rows"], list) or not value["rows"]:
        raise ContractError("population snapshot rows must be a non-empty array")
    return value


def public_access() -> dict[str, object]:
    return {
        "access_type": "public",
        "permission_confirmed": True,
        "permitted_uses": ["audience-composition", "population-framing"],
    }


def source_metadata(snapshot: dict[str, object]) -> dict[str, object]:
    source = snapshot["source"]
    if not isinstance(source, dict):
        raise ContractError("population snapshot source must be an object")
    expected = {
        "publisher",
        "program",
        "edition",
        "vintage",
        "retrieved_at",
        "source_url",
    }
    if set(source) != expected:
        raise ContractError("population snapshot source fields are not canonical")
    return {
        key: source[key]
        for key in ("publisher", "program", "edition", "vintage", "retrieved_at")
    }


def source_url(snapshot: dict[str, object]) -> str:
    source = snapshot["source"]
    assert isinstance(source, dict)
    value = source["source_url"]
    if not isinstance(value, str) or not value.startswith(("http://", "https://")):
        raise ContractError("population snapshot source_url must be HTTP(S)")
    return value


def finish_batch(batch: dict[str, object]) -> dict[str, object]:
    """Bind the normalized hash, then enforce the authoritative Task 3 contract."""

    hash_input = deepcopy(batch)
    hash_input.pop("normalized_batch_sha256", None)
    batch["normalized_batch_sha256"] = sha256_json(hash_input)
    try:
        return validate_observation_batch(batch)
    except ValueError as exc:
        raise ContractError(str(exc)) from exc
