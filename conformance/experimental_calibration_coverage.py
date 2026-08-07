"""Executable evidence for persona-calibration DGP coverage claims."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Mapping

from audience_panel_builder.common import sha256_json


class CoverageEvidenceError(ValueError):
    """Raised when a coverage claim lacks an executable witness."""


_PROBE_ID = "fixture-dgp-signature-and-evaluation-v1"
_KNOWN_BUT_OPTIONAL_PROBE_ID = "fixture-binding-only-v1"
_REQUIRED_DGP_CLASSES = {
    "nonlinear_saturation",
    "delayed_censored",
    "heavy_tailed",
    "zero_inflated",
}
_DGP_BINDINGS = {
    "nonlinear-saturation": (
        "nonlinear-saturation",
        "nonlinear_saturation",
    ),
    "delayed-censored-outcomes": (
        "delayed-censored-outcomes",
        "delayed_censored",
    ),
    "heavy-tailed-revenue": (
        "heavy-tailed-revenue",
        "heavy_tailed",
    ),
    "zero-inflated-value": (
        "zero-inflated-value",
        "zero_inflated",
    ),
}


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise CoverageEvidenceError(message)


def _json(path: Path) -> object:
    return json.loads(path.read_bytes())


def _signature_observed(
    observations: list[Mapping[str, object]],
    signature: Mapping[str, object],
) -> bool:
    rates = [
        float(row["outcome_events"]["conversions"])
        / float(row["delivery"]["impressions"])
        for row in observations
    ]
    assertion = signature.get("assertion")
    if assertion == "high_impression_rate_lt_low_impression_rate":
        low = [
            rate
            for row, rate in zip(observations, rates)
            if int(row["delivery"]["impressions"])
            <= int(signature["low_impression_max"])
        ]
        high = [
            rate
            for row, rate in zip(observations, rates)
            if int(row["delivery"]["impressions"])
            >= int(signature["high_impression_min"])
        ]
        return bool(low and high and sum(high) / len(high) < sum(low) / len(low))
    if assertion == "late_blocks_all_zero_and_early_blocks_not_all_zero":
        boundary = int(signature["late_block_min"])
        early: list[float] = []
        late: list[float] = []
        for row, rate in zip(observations, rates):
            block_number = int(
                str(row["experiment_binding"]["block"]).rsplit("-", 1)[1]
            )
            (late if block_number >= boundary else early).append(rate)
        return bool(early and late and max(late) == 0.0 and max(early) > 0.0)
    zero_share = sum(rate == 0.0 for rate in rates) / len(rates)
    max_rate = max(rates)
    if assertion == "zero_share_gt_and_max_rate_gt":
        return (
            zero_share > float(signature["zero_share_min_exclusive"])
            and max_rate > float(signature["max_rate_min_exclusive"])
        )
    if assertion == "zero_share_between_and_max_rate_between":
        return (
            float(signature["zero_share_min_inclusive"])
            <= zero_share
            <= float(signature["zero_share_max_inclusive"])
            and float(signature["max_rate_min_inclusive"])
            <= max_rate
            <= float(signature["max_rate_max_inclusive"])
        )
    raise CoverageEvidenceError(f"unknown signature assertion: {assertion}")


def _run_probe(
    cell: Mapping[str, object],
    fixture_root: Path,
    evaluation: Mapping[str, object],
) -> dict[str, object]:
    scenario_id = str(cell["scenario_family_id"])
    public = fixture_root / "open" / scenario_id
    scenario_manifest = _json(public / "scenario-manifest.json")
    observations = _json(public / "canonical-observations.json")
    oracle = _json(
        fixture_root / "oracle" / "open" / scenario_id / "hidden-oracle.json"
    )
    _require(isinstance(scenario_manifest, dict), "scenario manifest is not an object")
    _require(isinstance(observations, list), "observations are not an array")
    _require(isinstance(oracle, dict), "oracle is not an object")
    _require(
        scenario_manifest["scenario_binding"]["dgp_id"] == cell["dgp_id"],
        "cell DGP ID does not match the committed scenario manifest",
    )
    family = next(
        (
            row
            for row in evaluation["scenario_family_results"]
            if row["scenario_family_id"] == scenario_id
        ),
        None,
    )
    _require(family is not None, "evaluation did not execute the declared family")
    result = family["scenario_results"][0]
    return {
        "fixture_dgp_signature": _signature_observed(
            observations, cell["signature"]
        ),
        "oracle_failure_mechanism": oracle["failure_mechanism"]["kind"],
        "expected_action": result["expected_action"],
        "actual_action": result["actual_action"],
        "evaluation_result": result["result"],
        "candidate_count": len(result["engine_binding"]["candidate_bindings"]),
    }


def execute_coverage_matrix(
    matrix: Mapping[str, object],
    *,
    fixture_root: Path,
    evaluation: Mapping[str, object],
) -> list[dict[str, object]]:
    """Validate the claim matrix and return independently observed witnesses."""

    candidate = dict(matrix)
    supplied_hash = candidate.get("coverage_matrix_sha256")
    candidate["coverage_matrix_sha256"] = None
    _require(sha256_json(candidate) == supplied_hash, "coverage matrix hash mismatch")
    _require(
        matrix.get("schema_version")
        == "synthetic-persona-behavior-coverage-matrix-v2",
        "unsupported coverage matrix schema",
    )
    declared = matrix.get("declared_probe_ids")
    _require(isinstance(declared, list) and declared, "probe declarations are required")
    known = {_PROBE_ID, _KNOWN_BUT_OPTIONAL_PROBE_ID}
    _require(set(declared) <= known, "coverage matrix declares an unknown probe")

    executed_probe_ids: set[str] = set()
    evidence: list[dict[str, object]] = []
    included = 0
    for row in matrix["rows"]:
        cells = row.get("cells")
        _require(isinstance(cells, list), "coverage row cells must be an array")
        if row.get("coverage_status") == "excluded":
            _require(bool(row.get("exclusion_reason")), "excluded row needs a reason")
            _require(not cells, "excluded row cannot claim DGP cells")
            continue
        _require(
            row.get("coverage_status") == "dgp_generalized",
            "unknown coverage status",
        )
        included += 1
        classes = [cell.get("dgp_class") for cell in cells]
        scenario_ids = [cell.get("scenario_family_id") for cell in cells]
        dgp_ids = [cell.get("dgp_id") for cell in cells]
        _require(len(set(classes)) == len(classes), "duplicate DGP classes in behavior")
        _require(
            len(set(scenario_ids)) == len(scenario_ids),
            "duplicate scenario families in behavior",
        )
        _require(len(set(dgp_ids)) == len(dgp_ids), "duplicate DGP IDs in behavior")
        _require(len(set(classes)) >= 3, "behavior has fewer than three DGP classes")
        _require(
            _REQUIRED_DGP_CLASSES <= set(classes),
            "behavior omits a required incompatible DGP class",
        )
        for cell in cells:
            expected_binding = _DGP_BINDINGS.get(
                str(cell.get("scenario_family_id"))
            )
            _require(
                expected_binding
                == (cell.get("dgp_id"), cell.get("dgp_class")),
                "scenario family, DGP ID, and DGP class binding mismatch",
            )
            _require(
                cell.get("behavior_id") == row.get("behavior_id"),
                "cell behavior binding mismatch",
            )
            probe_id = cell.get("probe_id")
            _require(probe_id in known, "coverage cell uses an unknown probe")
            _require(
                probe_id == _PROBE_ID,
                "declared coverage probe was not executable",
            )
            observed = _run_probe(cell, fixture_root, evaluation)
            _require(
                observed == cell.get("expected_observation"),
                f"observed evidence mismatch for {row['behavior_id']} / "
                f"{cell['scenario_family_id']}",
            )
            executed_probe_ids.add(str(probe_id))
            evidence.append(
                {
                    "behavior_id": row["behavior_id"],
                    "scenario_family_id": cell["scenario_family_id"],
                    "dgp_id": cell["dgp_id"],
                    "dgp_class": cell["dgp_class"],
                    "probe_id": probe_id,
                    "observed": observed,
                }
            )
    _require(included > 0, "matrix has no DGP-generalized behaviors")
    _require(
        executed_probe_ids == set(declared),
        "a declared coverage probe was not executed",
    )
    return evidence
