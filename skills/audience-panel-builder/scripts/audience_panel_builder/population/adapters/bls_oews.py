"""BLS OEWS May 2025 pinned-snapshot adapter."""

from __future__ import annotations

from ...common import ContractError, sha256_json
from .base import (
    OBSERVATION_BATCH_VERSION,
    PinnedSnapshotAdapter,
    finish_batch,
    public_access,
    require_mapping,
    require_snapshot,
    source_metadata,
    source_url,
)


class BlsOewsAdapter(PinnedSnapshotAdapter):
    DESCRIPTOR = {
        "adapter_id": "bls-oews-may-2025",
        "programs": ["Occupational Employment and Wage Statistics"],
        "units": ["persons"],
        "dimensions": ["geography", "industry", "occupation"],
        "joints": [
            ["geography", "occupation"],
            ["industry", "occupation"],
        ],
        "geographies": ["US"],
        "access": {
            "access_type": "public",
            "evidence_basis": "public",
            "required_capability": "public-adapter",
        },
        "authentication": {"mode": "none", "required": False},
        "freshness": {
            "edition": "May 2025",
            "vintage": "2025-05-01",
            "published_at": "2026-05-15",
        },
        "implementation": (
            "audience_panel_builder.population.adapters.bls_oews:BlsOewsAdapter"
        ),
    }

    def normalize(
        self,
        raw_snapshot: dict[str, object],
        mapping: dict[str, object] | None = None,
    ) -> dict[str, object]:
        snapshot = require_snapshot(
            raw_snapshot,
            keys={
                "snapshot_version",
                "source",
                "geography",
                "unit",
                "denominator",
                "rows",
            },
            unit="persons",
        )
        mapping = require_mapping(mapping, {"batch_id", "frame_request_id"})
        url = source_url(snapshot)
        cells = []
        for index, raw in enumerate(snapshot["rows"]):
            if not isinstance(raw, dict) or set(raw) != {
                "area",
                "occupation_code",
                "occupation",
                "employment",
                "employment_rse_percent",
            }:
                raise ContractError(f"OEWS row {index} fields are not canonical")
            estimate = raw["employment"]
            rse = raw["employment_rse_percent"]
            if (
                isinstance(estimate, bool)
                or not isinstance(estimate, (int, float))
                or isinstance(rse, bool)
                or not isinstance(rse, (int, float))
            ):
                raise ContractError(f"OEWS row {index} estimate and RSE must be numeric")
            margin = float(estimate) * float(rse) / 100 * 1.96
            code = str(raw["occupation_code"])
            area = str(raw["area"])
            cells.append(
                {
                    "cell_id": f"occupation-{code}-{area.lower()}",
                    "dimension_values": {
                        "geography": area,
                        "occupation": code,
                    },
                    "estimate": float(estimate),
                    "uncertainty": {
                        "lower": round(float(estimate) - margin, 3),
                        "upper": round(float(estimate) + margin, 3),
                        "method": "relative-standard-error-95-percent",
                    },
                    "suppressed": False,
                    "status": "modeled",
                    "relationship": "joint",
                    "source_location": f"{url}#occupation={code}",
                }
            )
        batch = {
            "schema_version": OBSERVATION_BATCH_VERSION,
            "batch_id": mapping["batch_id"],
            "frame_request_id": mapping["frame_request_id"],
            "adapter_id": self.DESCRIPTOR["adapter_id"],
            "source_family": "public-government",
            "source": source_metadata(snapshot),
            "raw_snapshot_sha256": sha256_json(snapshot),
            "normalized_batch_sha256": "",
            "access": public_access(),
            "geography": list(snapshot["geography"]),
            "unit": snapshot["unit"],
            "denominator": snapshot["denominator"],
            "dimensions": ["geography", "occupation"],
            "cells": cells,
            "selection_notes": (
                "Pinned May 2025 national OEWS occupation estimates selected "
                "by unit and dimensions."
            ),
            "coverage_notes": (
                "Employer-reported employment estimates exclude self-employed workers."
            ),
            "citations": [url],
        }
        return finish_batch(batch)
