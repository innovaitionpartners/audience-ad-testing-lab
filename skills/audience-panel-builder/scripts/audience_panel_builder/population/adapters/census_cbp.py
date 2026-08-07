"""Census CBP 2023 pinned-snapshot adapter."""

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


class CensusCbpAdapter(PinnedSnapshotAdapter):
    DESCRIPTOR = {
        "adapter_id": "census-cbp-2023",
        "programs": ["County Business Patterns"],
        "units": ["establishments"],
        "dimensions": ["establishment-size", "geography", "industry"],
        "joints": [
            ["establishment-size", "industry"],
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
            "edition": "2023",
            "vintage": "2023-12-31",
            "published_at": "2025-06-26",
        },
        "implementation": (
            "audience_panel_builder.population.adapters.census_cbp:"
            "CensusCbpAdapter"
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
            unit="establishments",
        )
        mapping = require_mapping(mapping, {"batch_id", "frame_request_id"})
        url = source_url(snapshot)
        row_keys = {
            "naics",
            "industry",
            "legal_form",
            "employment_size",
            "establishments",
        }
        cells = []
        for index, raw in enumerate(snapshot["rows"]):
            if not isinstance(raw, dict) or set(raw) != row_keys:
                raise ContractError(f"CBP row {index} fields are not canonical")
            published_value = raw["establishments"]
            suppressed = published_value == "N"
            estimate = None if suppressed else published_value
            if suppressed:
                pass
            elif isinstance(estimate, bool) or not isinstance(estimate, (int, float)):
                raise ContractError(
                    f"CBP row {index} establishments must be numeric or N"
                )
            naics = str(raw["naics"])
            size = str(raw["employment_size"])
            cell_naics = naics.rstrip("-")
            lower = None if estimate is None else float(estimate)
            upper = None if estimate is None else float(estimate)
            cells.append(
                {
                    "cell_id": (
                        f"industry-{cell_naics}-establishment-size-{size}-us"
                    ),
                    "dimension_values": {
                        "geography": "US",
                        "industry": naics,
                        "establishment-size": size,
                    },
                    "estimate": None if estimate is None else float(estimate),
                    "uncertainty": {
                        "lower": lower,
                        "upper": upper,
                        "method": (
                            "not-available-suppression-n"
                            if suppressed
                            else "published-establishment-count-no-interval"
                        ),
                    },
                    "suppressed": suppressed,
                    "status": "missing" if suppressed else "observed",
                    "relationship": "joint",
                    "source_location": (
                        f"{url}#naics={naics}&establishment-size={size}"
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
            "dimensions": ["geography", "industry", "establishment-size"],
            "cells": cells,
            "selection_notes": (
                "Pinned 2023 CBP U.S. establishment rows selected by "
                "establishment unit."
            ),
            "coverage_notes": (
                "Establishment counts remain distinct from firm and person "
                "counts; suppressed cells stay missing."
            ),
            "citations": [url],
        }
        return finish_batch(batch)
