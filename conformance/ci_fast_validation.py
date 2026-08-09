#!/usr/bin/env python3
"""Closed CI partitions for the required private-stage validation surface."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

PARTITIONS: dict[str, tuple[str, ...]] = {
    "workflow-contracts": (
        "conformance.test_task9_integration."
        "RuntimeAndPackageIntegrationTests."
        "test_fast_ci_runs_every_private_stage_gate_without_node_syntax_check",
        "conformance.test_task9_integration."
        "RuntimeAndPackageIntegrationTests."
        "test_split_fast_ci_is_closed_parallel_and_fail_safe",
    ),
    "smoke": (
        "conformance.test_package",
        "conformance.test_audience_data_lab",
        "conformance.test_audience_panel_builder",
        "conformance.test_audience_prompt_contracts",
    ),
    "outcome-release": (
        "conformance.test_real_world_outcome_data_prep_contracts",
        "conformance.test_real_world_outcome_data_prep_runtime_guard",
        "conformance.test_real_world_outcome_data_prep_source_safety",
        "conformance.test_real_world_outcome_data_prep_adapters",
        "conformance.test_real_world_outcome_data_prep_publication",
        "conformance.test_real_world_outcome_data_prep_validation_handoff",
        "conformance.test_real_world_outcome_data_prep_golden_paths",
        "conformance.test_real_world_outcome_data_prep_skill_contract",
    ),
    "calibration-engine-and-evaluation": (
        "conformance.test_experimental_calibration_simulation",
        "conformance.test_experimental_calibration_adapters",
        "conformance.test_experimental_calibration_attributes",
        "conformance.test_experimental_calibration_evidence_library",
        "conformance.test_experimental_calibration_diagnosis",
        "conformance.test_experimental_calibration_exercise",
        "conformance.test_experimental_calibration_evaluation",
    ),
    "calibration-contracts-and-lifecycle": (
        "conformance.test_experimental_calibration_contracts",
        "conformance.test_experimental_calibration_candidate",
        "conformance.test_experimental_calibration_golden_paths",
        "conformance.test_experimental_calibration_public_claims",
        "conformance.test_experimental_calibration_real_world",
    ),
}

EXPECTED_TEST_COUNTS = {
    "workflow-contracts": 2,
    "smoke": 80,
    "outcome-release": 360,
    "calibration-engine-and-evaluation": 182,
    "calibration-contracts-and-lifecycle": 86,
}

# This anti-tautology coverage suite remains in full conformance. Listing the
# exclusion here makes the PR boundary explicit and causes new matching modules
# to fail closed until they are deliberately assigned.
FULL_CONFORMANCE_ONLY = {
    "conformance.test_experimental_calibration_coverage": (
        "full-conformance anti-tautology coverage proof"
    ),
}


class PartitionError(ValueError):
    """Raised when the closed CI partition inventory drifts."""


def _module_name(path: Path) -> str:
    return f"conformance.{path.stem}"


def _discover(pattern: str) -> set[str]:
    return {
        _module_name(path)
        for path in (ROOT / "conformance").glob(pattern)
        if path.is_file()
    }


def _require_exact_inventory(
    *, label: str, observed: set[str], expected: set[str]
) -> None:
    missing = sorted(expected - observed)
    unclassified = sorted(observed - expected)
    if missing or unclassified:
        details = []
        if missing:
            details.append("missing: " + ", ".join(missing))
        if unclassified:
            details.append("unclassified: " + ", ".join(unclassified))
        raise PartitionError(f"{label} partition drift ({'; '.join(details)})")


def validate_partition_inventory() -> dict[str, object]:
    owners: dict[str, str] = {}
    for partition, modules in PARTITIONS.items():
        for module in modules:
            previous = owners.setdefault(module, partition)
            if previous != partition:
                raise PartitionError(
                    f"module appears in multiple partitions: {module} "
                    f"({previous}, {partition})"
                )

    outcome = set(PARTITIONS["outcome-release"])
    _require_exact_inventory(
        label="outcome",
        observed=_discover("test_real_world_outcome_data_prep_*.py"),
        expected=outcome,
    )

    calibration_partitions = {
        "calibration-engine-and-evaluation",
        "calibration-contracts-and-lifecycle",
    }
    calibration = {
        module
        for partition in calibration_partitions
        for module in PARTITIONS[partition]
    }
    if calibration & set(FULL_CONFORMANCE_ONLY):
        raise PartitionError("full-conformance-only calibration module is in a PR shard")
    _require_exact_inventory(
        label="calibration",
        observed=_discover("test_experimental_calibration_*.py"),
        expected=calibration | set(FULL_CONFORMANCE_ONLY),
    )

    return {
        "partitions": {
            name: {
                "expected_tests": EXPECTED_TEST_COUNTS[name],
                "modules": list(modules),
            }
            for name, modules in PARTITIONS.items()
        },
        "full_conformance_only": FULL_CONFORMANCE_ONLY,
    }


def verify_release_identity() -> dict[str, str]:
    scripts = ROOT / "skills" / "real-world-outcome-data-prep" / "scripts"
    sys.path.insert(0, str(scripts))
    from outcome_data_prep.runtime_guard import (  # noqa: PLC0415
        load_release_manifest,
        verify_runtime_identity as verify,
    )

    manifest_path = (
        ROOT
        / "skills"
        / "real-world-outcome-data-prep"
        / "references"
        / "runtime-release-manifest.json"
    )
    identity = verify(
        plugin_root=ROOT,
        release_manifest=load_release_manifest(manifest_path),
        operation="import_results",
    )
    return {
        "release_tree_sha256": identity.release_tree_sha256,
        "release_version": identity.release_version,
        "repository": identity.repository,
    }


def run_partition(name: str) -> int:
    validate_partition_inventory()
    loader = unittest.defaultTestLoader
    suite = loader.loadTestsFromNames(PARTITIONS[name])
    observed = suite.countTestCases()
    expected = EXPECTED_TEST_COUNTS[name]
    if observed != expected:
        raise PartitionError(
            f"{name} test-count drift: expected {expected}, observed {observed}"
        )
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    return 0 if result.wasSuccessful() else 1


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("verify-inventory")
    subparsers.add_parser("verify-release-identity")
    run = subparsers.add_parser("run")
    run.add_argument("partition", choices=tuple(PARTITIONS))
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "verify-inventory":
            print(json.dumps(validate_partition_inventory(), sort_keys=True))
            return 0
        if args.command == "verify-release-identity":
            print(json.dumps(verify_release_identity(), sort_keys=True))
            return 0
        return run_partition(args.partition)
    except PartitionError as exc:
        print(f"CI partition validation failed: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
