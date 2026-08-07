"""Adapter for approved, already-aggregate Audience Data Lab evidence."""

from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import re
import sys

from ...common import ContractError, sha256_json
from .base import OBSERVATION_BATCH_VERSION, finish_batch, require_mapping


SKILLS_ROOT = Path(__file__).resolve().parents[5]
DATA_LAB_SCRIPTS = SKILLS_ROOT / "audience-data-lab" / "scripts"
if str(DATA_LAB_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(DATA_LAB_SCRIPTS))

from audience_data_lab.common import ContractError as DataLabContractError  # noqa: E402
from audience_data_lab.pipeline import validate_handoff  # noqa: E402


_MAPPING_KEYS = {
    "batch_id",
    "frame_request_id",
    "geography",
    "unit",
    "denominator",
    "dimensions",
    "estimate_field",
}


def _slug(value: object) -> str:
    text = re.sub(r"[^a-z0-9]+", "-", str(value).casefold()).strip("-")
    return text or "missing"


class AggregateEvidenceAdapter:
    DESCRIPTOR = {
        "adapter_id": "approved-aggregate-evidence-handoff",
        "programs": ["audience-first-party-evidence-v1"],
        "units": ["eligible-cohort-member"],
        "dimensions": ["company-size", "role"],
        "joints": [["company-size", "role"]],
        "geographies": ["US"],
        "access": {
            "access_type": "authorized",
            "evidence_basis": "first_party_aggregate",
            "required_capability": "approved-aggregate-handoff",
        },
        "authentication": {"mode": "approved-handoff", "required": True},
        "freshness": {
            "edition": "v1",
            "vintage": "2026-07-24",
            "published_at": "2026-07-24",
        },
        "implementation": (
            "audience_panel_builder.population.adapters.aggregate_evidence:"
            "AggregateEvidenceAdapter"
        ),
    }

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
            "aggregate_only": True,
            "capabilities": deepcopy(capabilities),
        }

    def acquire(
        self,
        dataset_plan: dict[str, object],
        destination: Path,
    ) -> dict[str, object]:
        raise ContractError(
            "aggregate evidence adapter accepts a supplied approved handoff only"
        )

    def normalize(
        self,
        raw_snapshot: dict[str, object],
        mapping: dict[str, object] | None = None,
    ) -> dict[str, object]:
        try:
            handoff = validate_handoff(raw_snapshot)
        except DataLabContractError as exc:
            raise ContractError(str(exc)) from exc
        if handoff["status"] != "approved" or not handoff["approval"][
            "approved_for_downstream_use"
        ]:
            raise ContractError("aggregate evidence handoff must be approved")
        if "audience_panel_research" not in handoff["allowed_uses"]:
            raise ContractError(
                "aggregate evidence handoff must allow audience_panel_research"
            )
        mapping = require_mapping(mapping, _MAPPING_KEYS)
        if mapping["estimate_field"] not in {"count", "share"}:
            raise ContractError("aggregate estimate_field must be count or share")
        dimensions = mapping["dimensions"]
        geography = mapping["geography"]
        if (
            not isinstance(dimensions, list)
            or not dimensions
            or any(not isinstance(item, str) or not item for item in dimensions)
            or not isinstance(geography, list)
            or not geography
            or any(not isinstance(item, str) or not item for item in geography)
        ):
            raise ContractError(
                "aggregate dimensions and geography must be non-empty string arrays"
            )
        source_cells = list(handoff.get("distributions", [])) + list(
            handoff.get("cross_tabs", [])
        )
        selected = [
            item
            for item in source_cells
            if isinstance(item, dict)
            and isinstance(item.get("dimensions"), dict)
            and set(item["dimensions"]) == set(dimensions)
        ]
        if not selected:
            raise ContractError(
                "approved aggregate handoff does not contain the requested dimensions"
            )
        estimate_field = mapping["estimate_field"]
        cells = []
        for index, item in enumerate(selected):
            if set(item) != {"dimensions", "count", "share", "suppressed"}:
                raise ContractError(
                    "approved aggregate cell fields are not canonical"
                )
            values = item["dimensions"]
            if any(
                not isinstance(values[dimension], str)
                for dimension in dimensions
            ):
                raise ContractError(
                    "approved aggregate dimension values must be strings"
                )
            suppressed = item["suppressed"]
            if not isinstance(suppressed, bool):
                raise ContractError(
                    "approved aggregate suppressed must be a boolean"
                )
            estimate = item[estimate_field]
            if suppressed:
                if estimate is not None:
                    raise ContractError(
                        "suppressed approved aggregate estimate must be null"
                    )
            elif isinstance(estimate, bool) or not isinstance(
                estimate, (int, float)
            ):
                raise ContractError(
                    "approved aggregate estimate must be numeric"
                )
            dimension_values = {
                dimension: values[dimension] for dimension in dimensions
            }
            cell_slug = "-".join(
                f"{_slug(dimension)}-{_slug(dimension_values[dimension])}"
                for dimension in dimensions
            )
            cells.append(
                {
                    "cell_id": f"aggregate-{cell_slug}-{index + 1}",
                    "dimension_values": dimension_values,
                    "estimate": None if estimate is None else float(estimate),
                    "uncertainty": {
                        "lower": (
                            None if estimate is None else float(estimate)
                        ),
                        "upper": (
                            None if estimate is None else float(estimate)
                        ),
                        "method": (
                            "suppressed-approved-aggregate"
                            if suppressed
                            else "exact-approved-aggregate-for-covered-cohort"
                        ),
                    },
                    "suppressed": suppressed,
                    "status": (
                        "missing"
                        if suppressed
                        else "derived"
                        if estimate_field == "share"
                        else "observed"
                    ),
                    "relationship": (
                        "joint" if len(dimensions) > 1 else "marginal"
                    ),
                    "source_location": (
                        f"{handoff['schema_version']}#{'cross-tabs' if len(dimensions) > 1 else 'distributions'}[{index}]"
                    ),
                }
            )
        time_window = handoff.get("time_window", {})
        vintage = (
            time_window.get("end")
            if isinstance(time_window, dict) and time_window.get("end")
            else str(handoff["created_at"])[:10]
        )
        batch = {
            "schema_version": OBSERVATION_BATCH_VERSION,
            "batch_id": mapping["batch_id"],
            "frame_request_id": mapping["frame_request_id"],
            "adapter_id": self.DESCRIPTOR["adapter_id"],
            "source_family": "authorized-aggregate",
            "source": {
                "publisher": "Authorized data owner",
                "program": "Audience Data Lab approved aggregate evidence",
                "edition": handoff["package_id"],
                "vintage": vintage,
                "retrieved_at": handoff["created_at"],
            },
            "raw_snapshot_sha256": sha256_json(handoff),
            "normalized_batch_sha256": "",
            "access": {
                "access_type": "authorized",
                "permission_confirmed": True,
                "permitted_uses": ["audience-composition"],
            },
            "geography": list(geography),
            "unit": mapping["unit"],
            "denominator": mapping["denominator"],
            "dimensions": list(dimensions),
            "cells": cells,
            "selection_notes": (
                "Only approved aggregate distributions or cross-tabs were selected."
            ),
            "coverage_notes": str(handoff["covered_population"]),
            "citations": [
                f"{handoff['schema_version']}#{handoff['package_id']}"
            ],
        }
        return finish_batch(batch)
