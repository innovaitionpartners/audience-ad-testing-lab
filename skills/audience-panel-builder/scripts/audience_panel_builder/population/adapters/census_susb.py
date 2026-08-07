"""Census SUSB 2022 pinned-snapshot adapter."""

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


class CensusSusbAdapter(PinnedSnapshotAdapter):
    DESCRIPTOR = {
        "adapter_id": "census-susb-2022",
        "programs": ["Statistics of U.S. Businesses"],
        "units": ["firms"],
        "dimensions": ["enterprise-size", "geography", "industry"],
        "joints": [
            ["enterprise-size", "industry"],
            ["geography", "industry"],
        ],
        "geographies": ["US"],
        "access": {
            "access_type": "public",
            "evidence_basis": "public",
            "required_capability": "public-adapter",
        },
        "authentication": {"mode": "none", "required": False},
        "freshness": {
            "edition": "2022",
            "vintage": "2022-12-31",
            "published_at": "2025-04-10",
        },
        "implementation": (
            "audience_panel_builder.population.adapters.census_susb:"
            "CensusSusbAdapter"
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
            unit="firms",
        )
        mapping = require_mapping(mapping, {"batch_id", "frame_request_id"})
        url = source_url(snapshot)
        cells = []
        row_keys = {
            "naics",
            "industry",
            "enterprise_size_code",
            "enterprise_size",
            "firms",
        }
        for index, raw in enumerate(snapshot["rows"]):
            if not isinstance(raw, dict) or set(raw) != row_keys:
                raise ContractError(f"SUSB row {index} fields are not canonical")
            estimate = raw["firms"]
            if isinstance(estimate, bool) or not isinstance(estimate, (int, float)):
                raise ContractError(f"SUSB row {index} firms must be numeric")
            naics = str(raw["naics"])
            size = str(raw["enterprise_size_code"])
            cells.append(
                {
                    "cell_id": (
                        f"industry-{naics}-enterprise-size-{size}-us"
                    ),
                    "dimension_values": {
                        "geography": "US",
                        "industry": naics,
                        "enterprise-size": size,
                    },
                    "estimate": float(estimate),
                    "uncertainty": {
                        "lower": float(estimate),
                        "upper": float(estimate),
                        "method": "published-firm-count-no-interval",
                    },
                    "suppressed": False,
                    "status": "observed",
                    "relationship": "joint",
                    "source_location": (
                        f"{url}#naics={naics}&enterprise-size={size}"
                    ),
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
            "dimensions": ["geography", "industry", "enterprise-size"],
            "cells": cells,
            "selection_notes": (
                "Pinned 2022 SUSB enterprise employment-size rows selected "
                "by firm unit."
            ),
            "coverage_notes": (
                "Firm counts remain distinct from establishment and employment counts."
            ),
            "citations": [url],
        }
        return finish_batch(batch)
