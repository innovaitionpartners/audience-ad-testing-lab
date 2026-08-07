"""Adapter for hash-bound Task 2 Audience Data Lab handoffs."""

from __future__ import annotations

import json
from pathlib import Path
import sys

from ...common import ContractError


SKILLS_ROOT = Path(__file__).resolve().parents[5]
DATA_LAB_SCRIPTS = SKILLS_ROOT / "audience-data-lab" / "scripts"
RESEARCH_SCRIPTS = SKILLS_ROOT / "audience-ad-testing-lab" / "scripts"
for scripts in (DATA_LAB_SCRIPTS, RESEARCH_SCRIPTS):
    if str(scripts) not in sys.path:
        sys.path.insert(0, str(scripts))

from audience_data_lab.authorized_transform import (  # noqa: E402
    validate_authorized_handoff,
)
from audience_data_lab.common import ContractError as DataLabContractError  # noqa: E402
from audience_lab.audience_research_v3 import (  # noqa: E402
    validate_observation_batch,
)


class AuthorizedHandoffAdapter:
    DESCRIPTOR = {
        "adapter_id": "authorized-audience-data-lab-handoff",
        "programs": ["authorized-audience-handoff-v1"],
        "units": ["eligible-cohort-member"],
        "dimensions": ["company-size", "role"],
        "joints": [["company-size", "role"]],
        "geographies": ["US"],
        "access": {
            "access_type": "authorized",
            "evidence_basis": "first_party_aggregate",
            "required_capability": "authorized-handoff",
        },
        "authentication": {
            "mode": "audience-data-lab-handoff",
            "required": True,
        },
        "freshness": {
            "edition": "v1",
            "vintage": "2026-07-24",
            "published_at": "2026-07-24",
        },
        "implementation": (
            "audience_panel_builder.population.adapters.authorized_handoff:"
            "AuthorizedHandoffAdapter"
        ),
    }

    def descriptor(self) -> dict[str, object]:
        return json.loads(json.dumps(self.DESCRIPTOR))

    def plan(
        self,
        frame_request: dict[str, object],
        capabilities: dict[str, object],
    ) -> dict[str, object]:
        return {
            "adapter_id": self.DESCRIPTOR["adapter_id"],
            "frame_request_id": frame_request.get("request_id"),
            "validation_only": True,
            "capabilities": json.loads(json.dumps(capabilities)),
        }

    def acquire(
        self,
        dataset_plan: dict[str, object],
        destination: Path,
    ) -> dict[str, object]:
        raise ContractError(
            "authorized handoff adapter does not acquire source files"
        )

    def normalize(
        self,
        raw_snapshot: dict[str, object],
        mapping: dict[str, object] | None = None,
    ) -> dict[str, object]:
        if not isinstance(raw_snapshot, dict) or set(raw_snapshot) != {
            "handoff",
            "output_root",
        }:
            raise ContractError(
                "authorized adapter accepts only canonical handoff and output_root"
            )
        if mapping is not None:
            raise ContractError(
                "authorized canonical observation batches do not accept remapping"
            )
        output_root_text = raw_snapshot["output_root"]
        if not isinstance(output_root_text, str) or not output_root_text:
            raise ContractError("authorized handoff output_root must be a path")
        output_root = Path(output_root_text)
        try:
            handoff = validate_authorized_handoff(
                raw_snapshot["handoff"],
                output_root=output_root,
            )
        except DataLabContractError as exc:
            raise ContractError(str(exc)) from exc
        candidates = [
            item
            for item in handoff["outputs"]
            if item["route"] == "structural_frame"
            and item["schema_version"]
            == "audience-frame-observation-batch-v1"
        ]
        if len(candidates) != 1:
            raise ContractError(
                "authorized handoff must contain exactly one structural frame batch"
            )
        output_path = output_root / candidates[0]["path"]
        try:
            payload = json.loads(output_path.read_text(encoding="utf-8"))
            return validate_observation_batch(payload)
        except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
            raise ContractError(
                "authorized handoff structural frame batch is invalid"
            ) from exc
