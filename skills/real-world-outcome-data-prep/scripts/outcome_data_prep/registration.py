"""Derive and seal pre-launch outcome-study identity records."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from copy import deepcopy
from dataclasses import dataclass
import json
import math
import os
from pathlib import Path
import re
import shutil
import stat
import sys
import tempfile

from .common import ContractError, canonical_json_bytes, sha256_bytes, sha256_json
from .contracts import (
    CREATIVE_MANIFEST_VERSION,
    DELIVERY_MAP_VERSION,
    validate_authenticated_registration_receipt,
    validate_creative_manifest,
    validate_delivery_map,
)
from .runtime_guard import require_approved_runtime
from .study_authority import (
    STUDY_RECEIPT_DOMAIN,
    StudyAuthorityError,
    authority_hmac,
    derive_evidence_status,
    study_receipt_projection,
)


PANEL_BUILDER_SCRIPTS = (
    Path(__file__).resolve().parents[3] / "audience-panel-builder" / "scripts"
)
if str(PANEL_BUILDER_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(PANEL_BUILDER_SCRIPTS))

from audience_panel_builder.population.validation.contracts import (  # noqa: E402
    approve_preregistration_design,
    load_trusted_authority_registry,
    read_protected_authority_secret,
    seal_preregistration,
    validate_preregistration,
)
from audience_panel_builder.population.validation.package import (  # noqa: E402
    _panel_snapshot,
    _rename_directory_no_replace,
)
from audience_panel_builder.population.validation.evidence_bindings import (  # noqa: E402
    LINEAGE_ORDER,
    lineage_bundle_sha256,
)
from audience_panel_builder.population.validation.evidence_errors import (  # noqa: E402
    ProducerEvidenceError,
)
from audience_panel_builder.population.validation.evidence_snapshot import (  # noqa: E402
    open_evidence_snapshot,
)
from audience_panel_builder.population.validation.producer_evidence import (  # noqa: E402
    validate_synthetic_producer_evidence,
)


REQUIRED_QUESTION_CODES = {
    "primary_metric",
    "metric_direction",
    "measurement_window",
    "attribution_window",
    "validation_blocks",
    "minimum_effect",
    "missing_data_rule",
    "permission_reference",
    "delivery_start_evidence",
    "outcome_access_attestation",
    "registered_by",
    "approved_by",
}
_QUESTION_ORDER = (
    "primary_metric",
    "metric_direction",
    "measurement_window",
    "attribution_window",
    "validation_blocks",
    "minimum_effect",
    "missing_data_rule",
    "permission_reference",
    "delivery_start_evidence",
    "outcome_access_attestation",
    "registered_by",
    "approved_by",
)
assert set(_QUESTION_ORDER) == REQUIRED_QUESTION_CODES

_DIGEST = re.compile(r"sha256:[0-9a-f]{64}\Z")
_SURFACE_IDENTITIES = {
    ("complete_exposure", "screening"): (
        "complete_exposure_ordering",
        "screening-model-results.json",
    ),
    ("partial_exposure_maxdiff", "screening"): (
        "maxdiff_screening_ordering",
        "screening-model-results.json",
    ),
    ("partial_exposure_maxdiff", "boundary"): (
        "pairwise_boundary_ordering",
        "boundary-results.json",
    ),
}


@dataclass(frozen=True)
class RegistrationDraft:
    preregistration: dict[str, object]
    delivery_map: dict[str, object]
    creative_manifest: dict[str, object]
    study_summary: str
    evidence_status: str
    unresolved_questions: tuple[str, ...]


@dataclass(frozen=True)
class SealedStudy:
    study_root: Path
    registration_sha256: str
    delivery_map_sha256: str
    creative_manifest_sha256: str
    receipt_sha256: str
    evidence_status: str


@dataclass(frozen=True)
class _RunIdentity:
    manifest: dict[str, object]
    roster: dict[str, object]
    testing_map: tuple[dict[str, object], ...]
    result: dict[str, object]
    result_name: str
    result_sha256: str
    result_bytes_sha256: str
    manifest_sha256: str
    run_id: str
    method: str
    stage: str
    creative_hashes: tuple[tuple[str, str], ...]
    lineage_bundle_sha256: str
    producer_evidence_sha256: str
    producer_semantics_sha256: str
    frozen_at: str
    producer_evidence_sealed_at: str


def _duplicate_free(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ContractError(f"duplicate JSON field: {key}")
        result[key] = value
    return result


def _reject_constant(value: str) -> None:
    raise ValueError(f"non-finite JSON constant: {value}")


def _finite_json(value: object, path: str = "$") -> None:
    if value is None or type(value) in {bool, int, str}:  # noqa: E721
        return
    if type(value) is float:  # noqa: E721
        if not math.isfinite(value):
            raise ContractError(f"{path} contains a non-finite number")
        return
    if isinstance(value, list):
        for index, item in enumerate(value):
            _finite_json(item, f"{path}[{index}]")
        return
    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str):
                raise ContractError(f"{path} has a non-string key")
            _finite_json(item, f"{path}.{key}")
        return
    raise ContractError(f"{path} contains a non-JSON value")


def _read_json_file(path: Path, label: str) -> tuple[dict[str, object], bytes]:
    selected = Path(path)
    try:
        before = selected.lstat()
        if stat.S_ISLNK(before.st_mode) or not stat.S_ISREG(before.st_mode):
            raise OSError("not a regular file")
        raw = selected.read_bytes()
        after = selected.lstat()
    except OSError as exc:
        raise ContractError(f"{label} is not a readable regular file") from exc
    if (
        before.st_dev,
        before.st_ino,
        before.st_size,
        before.st_mtime_ns,
        before.st_ctime_ns,
    ) != (
        after.st_dev,
        after.st_ino,
        after.st_size,
        after.st_mtime_ns,
        after.st_ctime_ns,
    ):
        raise ContractError(f"{label} changed while read")
    if len(raw) > 256 * 1024 * 1024:
        raise ContractError(f"{label} exceeds the input limit")
    try:
        value = json.loads(
            raw,
            object_pairs_hook=_duplicate_free,
            parse_constant=_reject_constant,
        )
    except (UnicodeError, json.JSONDecodeError, ValueError) as exc:
        raise ContractError(f"{label} is not duplicate-free finite UTF-8 JSON") from exc
    if not isinstance(value, dict):
        raise ContractError(f"{label} must contain an object")
    _finite_json(value, label)
    return value, raw


def _digest(value: object, label: str) -> str:
    if not isinstance(value, str) or _DIGEST.fullmatch(value) is None:
        raise ContractError(f"{label} must be a prefixed SHA-256")
    return value


def _nonempty(value: object, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ContractError(f"{label} must be a non-empty string")
    return value


def _derive_panel_binding(panel_package: Path) -> dict[str, object]:
    try:
        binding, _validation = _panel_snapshot(Path(panel_package))
    except ContractError:
        raise
    except (OSError, ValueError, TypeError) as exc:
        raise ContractError("panel package authentication failed") from exc
    return binding


def _creative_rows(
    roster: Mapping[str, object], manifest: Mapping[str, object]
) -> tuple[tuple[dict[str, object], ...], tuple[tuple[str, str], ...]]:
    raw_creatives = roster.get("creatives")
    if not isinstance(raw_creatives, list) or not raw_creatives:
        raise ContractError("creative roster must contain creatives")
    outputs = manifest.get("outputs")
    hashes = (
        outputs.get("creative_asset_hashes")
        if isinstance(outputs, Mapping)
        else None
    )
    if not isinstance(hashes, Mapping) or not hashes:
        raise ContractError(
            "authenticated run manifest must bind creative_asset_hashes"
        )
    rows: list[dict[str, object]] = []
    identities: list[tuple[str, str]] = []
    for index, raw_creative in enumerate(raw_creatives):
        if not isinstance(raw_creative, Mapping):
            raise ContractError(f"creative roster row {index} must be an object")
        creative_id = raw_creative.get(
            "variation_id", raw_creative.get("creative_id")
        )
        creative_id = _nonempty(creative_id, f"creative roster row {index} ID")
        if creative_id not in hashes:
            raise ContractError(
                "creative roster is not exactly bound by manifest asset hashes"
            )
        asset_sha = _digest(
            hashes[creative_id], f"manifest creative hash {creative_id}"
        )
        supplied_content = raw_creative.get("content_hash")
        if supplied_content is not None and supplied_content != asset_sha:
            raise ContractError(
                "creative roster content hash does not match the run manifest"
            )
        rows.append(dict(raw_creative))
        identities.append((creative_id, asset_sha))
    if len({creative_id for creative_id, _ in identities}) != len(identities):
        raise ContractError("creative roster contains duplicate creative IDs")
    if set(hashes) != {creative_id for creative_id, _ in identities}:
        raise ContractError(
            "manifest asset hashes must exactly cover the creative roster"
        )
    return tuple(rows), tuple(sorted(identities))


def _testing_rows(document: Mapping[str, object]) -> tuple[dict[str, object], ...]:
    rows = document.get("testing_map")
    if not isinstance(rows, list) or not rows:
        raise ContractError("testing map must contain a non-empty testing_map")
    checked: list[dict[str, object]] = []
    for index, raw_row in enumerate(rows):
        if not isinstance(raw_row, Mapping):
            raise ContractError(f"testing map row {index} must be an object")
        creative_id = _nonempty(
            raw_row.get("creative_id"), f"testing map row {index}.creative_id"
        )
        role = _nonempty(
            raw_row.get("role", "planned-creative"),
            f"testing map row {index}.role",
        )
        checked.append({**dict(raw_row), "creative_id": creative_id, "role": role})
    if len({str(row["creative_id"]) for row in checked}) != len(checked):
        raise ContractError("testing map contains duplicate creative IDs")
    return tuple(checked)


def _authenticate_run(
    run_root: Path,
    *,
    evidence_root: Path,
    snapshot_root: Path,
) -> _RunIdentity:
    root = Path(run_root)
    manifest, manifest_raw = _read_json_file(
        root / "study-manifest.json", "study manifest"
    )
    roster, _roster_raw = _read_json_file(
        root / "creative-roster.json", "creative roster"
    )
    if (root / "testing-map.json").is_file():
        testing_document, _testing_raw = _read_json_file(
            root / "testing-map.json", "testing map"
        )
    else:
        testing_document, _testing_raw = _read_json_file(
            root / "finalist-results.json", "finalist testing map"
        )
    result_name = (
        "boundary-results.json"
        if (root / "boundary-results.json").is_file()
        else "screening-model-results.json"
    )
    result, result_raw = _read_json_file(root / result_name, "producer result")
    run_id = _nonempty(manifest.get("study_id"), "study manifest study_id")
    if result.get("study_id") != run_id:
        raise ContractError("producer result study_id does not match run manifest")
    method = _nonempty(
        result.get("method", manifest.get("method")), "producer method"
    )
    stage = "boundary" if result_name == "boundary-results.json" else "screening"
    surface, expected_name = _SURFACE_IDENTITIES.get(
        (method, stage), (None, None)
    )
    if surface is None or expected_name != result_name:
        raise ContractError("producer method/stage surface is unsupported")
    result_sha256 = sha256_json(result)
    try:
        producer_receipt = validate_synthetic_producer_evidence(
            surface=surface,
            run_id=run_id,
            result_sha256=result_sha256,
            evidence_root=Path(evidence_root),
            snapshot_root=Path(snapshot_root),
        )
        with open_evidence_snapshot(
            surface=surface,
            run_id=run_id,
            result_sha256=result_sha256,
            snapshot_root=Path(snapshot_root),
        ) as snapshot:
            snapshot_binding = producer_receipt.get("snapshot_binding")
            if not isinstance(snapshot_binding, Mapping) or (
                snapshot.snapshot_id != snapshot_binding.get("snapshot_id")
                or snapshot.snapshot_sha256
                != snapshot_binding.get("snapshot_sha256")
                or snapshot.archive_sha256
                != snapshot_binding.get("archive_sha256")
                or snapshot.frozen_at != producer_receipt.get("frozen_at")
            ):
                raise ContractError(
                    "authenticated producer snapshot does not match its receipt"
                )
            snapshot_manifest, snapshot_manifest_raw = _read_json_file(
                snapshot.resolve_member("study_manifest"),
                "authenticated producer study manifest",
            )
            snapshot_result, snapshot_result_raw = _read_json_file(
                snapshot.resolve_member("result"),
                "authenticated producer result",
            )
    except (ProducerEvidenceError, ContractError, OSError, KeyError, TypeError, ValueError) as exc:
        raise ContractError("authenticated producer evidence failed") from exc

    input_bindings = producer_receipt.get("input_bindings")
    result_binding = producer_receipt.get("result_binding")
    semantics = producer_receipt.get("producer_semantics")
    if (
        not isinstance(input_bindings, Mapping)
        or not isinstance(result_binding, Mapping)
        or not isinstance(semantics, Mapping)
        or snapshot_manifest != manifest
        or snapshot_manifest_raw != manifest_raw
        or snapshot_result != result
        or snapshot_result_raw != result_raw
        or result_binding.get("canonical_document_sha256") != result_sha256
        or result_binding.get("raw_bytes_sha256") != sha256_bytes(result_raw)
    ):
        raise ContractError(
            "authenticated producer snapshot does not bind exact run bytes"
        )
    try:
        lineage_sha256 = lineage_bundle_sha256(
            {role: input_bindings[role] for role in LINEAGE_ORDER}  # type: ignore[misc]
        )
    except (ProducerEvidenceError, KeyError, TypeError, ValueError) as exc:
        raise ContractError("authenticated producer lineage is invalid") from exc
    creatives, creative_hashes = _creative_rows(roster, manifest)
    del creatives
    testing_map = _testing_rows(testing_document)
    creative_ids = {creative_id for creative_id, _ in creative_hashes}
    if not {str(row["creative_id"]) for row in testing_map}.issubset(creative_ids):
        raise ContractError("testing map references an unauthenticated creative")
    ranked_ids = result.get("ranked_ids")
    if (
        not isinstance(ranked_ids, list)
        or not ranked_ids
        or any(not isinstance(item, str) for item in ranked_ids)
        or not set(ranked_ids).issubset(creative_ids)
    ):
        raise ContractError("producer result ranking is not bound to the creative roster")
    return _RunIdentity(
        manifest=manifest,
        roster=roster,
        testing_map=testing_map,
        result=result,
        result_name=result_name,
        result_sha256=result_sha256,
        result_bytes_sha256=sha256_bytes(result_raw),
        manifest_sha256=sha256_json(manifest),
        run_id=run_id,
        method=method,
        stage=stage,
        creative_hashes=creative_hashes,
        lineage_bundle_sha256=lineage_sha256,
        producer_evidence_sha256=_digest(
            producer_receipt.get("producer_evidence_sha256"),
            "authenticated producer evidence",
        ),
        producer_semantics_sha256=_digest(
            semantics.get("producer_semantics_sha256"),
            "authenticated producer semantics",
        ),
        frozen_at=_nonempty(
            producer_receipt.get("frozen_at"),
            "authenticated producer frozen_at",
        ),
        producer_evidence_sealed_at=_nonempty(
            producer_receipt.get("sealed_at"),
            "authenticated producer sealed_at",
        ),
    )


def _load_campaign_plan(value: object, index: int) -> dict[str, object]:
    if isinstance(value, Mapping):
        plan = deepcopy(dict(value))
        _finite_json(plan, f"campaign_plans[{index}]")
        return plan
    if isinstance(value, (str, Path)):
        plan, _raw = _read_json_file(Path(value), f"campaign plan {index}")
        return plan
    raise ContractError(f"campaign plan {index} must be an object or JSON path")


def _event(
    event_type: str,
    *,
    occurred_at: object,
    evidence_source_sha256: object,
    attested_by: object,
    attested_at: object,
    authority_id: object,
) -> dict[str, object]:
    return {
        "event_type": event_type,
        "occurred_at": occurred_at,
        "evidence_source_sha256": evidence_source_sha256,
        "attested_by": attested_by,
        "attested_at": attested_at,
        "authority_id": authority_id,
    }


def _evidence_events(
    value: object, *, event_type: str
) -> list[dict[str, object]]:
    if value is None:
        return []
    values = value if isinstance(value, list) else [value]
    result: list[dict[str, object]] = []
    for index, raw in enumerate(values):
        if not isinstance(raw, Mapping):
            raise ContractError(
                f"{event_type} evidence {index} must be an object"
            )
        result.append(
            _event(
                str(raw.get("event_type", event_type)),
                occurred_at=raw.get("occurred_at"),
                evidence_source_sha256=raw.get("evidence_source_sha256"),
                attested_by=raw.get("attested_by"),
                attested_at=raw.get("attested_at"),
                authority_id=raw.get("authority_id"),
            )
        )
    return result


def _chronology(
    *,
    preregistration: Mapping[str, object],
    campaign_plans: Sequence[Mapping[str, object]],
    supplied_facts: Mapping[str, object],
) -> dict[str, object]:
    registered_by = supplied_facts.get(
        "registered_by", preregistration.get("registered_by")
    )
    approved_by = supplied_facts.get(
        "approved_by",
        (
            preregistration.get("approval", {}).get("approved_by")
            if isinstance(preregistration.get("approval"), Mapping)
            else None
        ),
    )
    del campaign_plans
    events: list[dict[str, object]] = []
    events.extend(
        _evidence_events(
            supplied_facts.get("delivery_start_evidence"),
            event_type="delivery_started",
        )
    )
    outcome = supplied_facts.get("outcome_access_attestation")
    if isinstance(outcome, Mapping):
        status = outcome.get("status")
        event_type = (
            "outcome_not_accessed"
            if status == "not_accessed"
            else "outcome_access_started"
        )
        events.extend(_evidence_events(outcome, event_type=event_type))
    elif outcome is not None:
        events.extend(
            _evidence_events(outcome, event_type="outcome_access_started")
        )
    first_access = supplied_facts.get("first_outcome_accessed_at")
    if first_access is not None:
        events.append(
            _event(
                "reported_outcome_access",
                occurred_at=first_access,
                evidence_source_sha256=sha256_json(
                    {"reported_outcome_accessed_at": first_access}
                ),
                attested_by=registered_by,
                attested_at=first_access,
                authority_id=approved_by,
            )
        )
    return {"events": events}


def _surface(
    run: _RunIdentity, template: Mapping[str, object]
) -> dict[str, object]:
    surface, expected_name = _SURFACE_IDENTITIES.get(
        (run.method, run.stage), (None, None)
    )
    if surface is None or expected_name != run.result_name:
        raise ContractError("producer method/stage surface is unsupported")
    outputs = run.manifest.get("outputs")
    creative_hashes = (
        outputs.get("creative_asset_hashes")
        if isinstance(outputs, Mapping)
        else {}
    )
    if not isinstance(creative_hashes, Mapping):
        raise ContractError("manifest creative hashes are invalid")
    del template
    return {
        "surface": surface,
        "method": run.method,
        "stage": run.stage,
        "run_id": run.run_id,
        "result_path": run.result_name,
        "result_sha256": run.result_sha256,
        "result_bytes_sha256": run.result_bytes_sha256,
        "manifest_sha256": run.manifest_sha256,
        "lineage_bundle_sha256": run.lineage_bundle_sha256,
        "producer_evidence_sha256": run.producer_evidence_sha256,
        "producer_semantics_sha256": run.producer_semantics_sha256,
        "frozen_at": run.frozen_at,
        "producer_evidence_sealed_at": run.producer_evidence_sealed_at,
        "eligible_creatives": [
            {
                "creative_id": creative_id,
                "creative_sha256": creative_sha,
            }
            for creative_id, creative_sha in run.creative_hashes
        ],
    }


def _build_preregistration(
    *,
    panel_binding: Mapping[str, object],
    run: _RunIdentity,
    campaign_plans: Sequence[Mapping[str, object]],
    supplied_facts: Mapping[str, object],
) -> dict[str, object]:
    raw_template = supplied_facts.get("preregistration_template")
    if isinstance(raw_template, Mapping):
        document = deepcopy(dict(raw_template))
    else:
        registration_id = str(
            supplied_facts.get(
                "registration_id", f"outcome-{run.run_id}"
            )
        )
        registered_at = supplied_facts.get(
            "registered_at",
            supplied_facts.get(
                "delivery_map_sealed_at",
                run.manifest.get("producer_evidence_sealed_at"),
            ),
        )
        approved_at = supplied_facts.get("approved_at", registered_at)
        validation_blocks = deepcopy(
            supplied_facts.get("validation_blocks", [])
        )
        if not isinstance(validation_blocks, list):
            validation_blocks = []
        block_ids = [
            str(block.get("block_id"))
            for block in validation_blocks
            if isinstance(block, Mapping) and block.get("block_id") is not None
        ]
        segment_blocks: dict[str, set[str]] = {}
        for raw_block in validation_blocks:
            if not isinstance(raw_block, Mapping):
                continue
            block_id = str(raw_block.get("block_id"))
            memberships = raw_block.get("planned_segment_membership")
            if not isinstance(memberships, list):
                continue
            for membership in memberships:
                if not isinstance(membership, Mapping):
                    continue
                segment_ids = membership.get("segment_ids")
                if not isinstance(segment_ids, list):
                    continue
                for segment_id in segment_ids:
                    if isinstance(segment_id, str):
                        segment_blocks.setdefault(segment_id, set()).add(block_id)
        segment_weights = supplied_facts.get("segment_weights")
        weights = (
            segment_weights if isinstance(segment_weights, Mapping) else {}
        )
        default_weight = 1.0 / max(1, len(segment_blocks))
        segments = [
            {
                "segment_id": segment_id,
                "must_cover": True,
                "effective_panel_weight": weights.get(
                    segment_id, default_weight
                ),
                "planned_block_ids": sorted(planned_blocks),
                "evidence_sha256": "sha256:" + "0" * 64,
                "approval_sha256": "sha256:" + "0" * 64,
            }
            for segment_id, planned_blocks in sorted(segment_blocks.items())
        ]
        first_plan = campaign_plans[0] if campaign_plans else {}
        raw_scope = supplied_facts.get("outcome_scope")
        if isinstance(raw_scope, Mapping):
            outcome_scope = deepcopy(dict(raw_scope))
        else:
            outcome_scope = {
                "cohort_id": str(
                    supplied_facts.get(
                        "cohort_id", panel_binding.get("panel_id")
                    )
                ),
                "segment_id": str(
                    supplied_facts.get(
                        "segment_id",
                        next(iter(segment_blocks), "all-segments"),
                    )
                ),
                "channel": str(
                    supplied_facts.get(
                        "channel", first_plan.get("platform", "paid-media")
                    )
                ),
                "placement": str(
                    supplied_facts.get(
                        "placement", first_plan.get("placement", "all-placements")
                    )
                ),
                "objective": str(
                    supplied_facts.get("objective", "registered-primary-metric")
                ),
                "geography": str(
                    supplied_facts.get("geography", "registered-geography")
                ),
                "validation_window": str(
                    supplied_facts.get(
                        "measurement_window", "registered-window"
                    )
                ),
            }
        primary = supplied_facts.get("primary_metric")
        if isinstance(primary, Mapping):
            metric = deepcopy(dict(primary))
        else:
            metric_name = str(primary or "registered-primary-metric")
            metric = {
                "name": metric_name,
                "definition": str(
                    supplied_facts.get(
                        "metric_definition",
                        f"Registered aggregate {metric_name}.",
                    )
                ),
                "direction": supplied_facts.get(
                    "metric_direction", "higher_is_better"
                ),
                "exposure_unit": str(
                    supplied_facts.get("exposure_unit", "eligible-exposure")
                ),
                "outcome_unit": str(
                    supplied_facts.get("outcome_unit", metric_name)
                ),
                "measurement_window": supplied_facts.get(
                    "measurement_window", "registered-window"
                ),
                "attribution_window": supplied_facts.get(
                    "attribution_window", "registered-attribution"
                ),
                "practical_equivalence_margin": supplied_facts.get(
                    "practical_equivalence_margin", 0.0
                ),
                "smallest_effect_of_interest": supplied_facts.get(
                    "minimum_effect", 0.0
                ),
            }
        surface_name = _SURFACE_IDENTITIES.get(
            (run.method, run.stage), ("", "")
        )[0]
        tie_handling = (
            {
                "ordering_equivalence": "exact-utility-equality-v1",
                "ordering_tiebreak": "creative-id-serialization-only-v1",
            }
            if surface_name == "complete_exposure_ordering"
            else {
                "ordering_equivalence": "rounded-utility-bucket-v1",
                "ordering_tiebreak": "creative-id-serialization-only-v1",
                "effective_ordering_tolerance": supplied_facts.get(
                    "effective_ordering_tolerance", 1e-8
                ),
                "rounding_rule": "python-half-even-v1",
            }
        )
        document = {
            "schema_version": "panel-validation-preregistration-v1",
            "registration_id": registration_id,
            "registered_at": registered_at,
            "registered_by": supplied_facts.get("registered_by"),
            "status": "registered",
            "panel_binding": deepcopy(dict(panel_binding)),
            "synthetic_surface": {
                "frozen_at": run.manifest.get("generated_at"),
                "producer_evidence_sealed_at": run.manifest.get(
                    "producer_evidence_sealed_at"
                ),
                "lineage_bundle_sha256": None,
                "producer_evidence_sha256": None,
                "producer_semantics_sha256": None,
            },
            "claim_scope": {
                "panel_binding": deepcopy(dict(panel_binding)),
                "synthetic_binding": {},
                "outcome_scope": outcome_scope,
            },
            "primary_metric": metric,
            "secondary_metrics": [],
            "validation_blocks": validation_blocks,
            "holdout_partition": {
                "partition_unit": "block",
                "held_out_ids": sorted(block_ids),
            },
            "prior_outcome_access": [],
            "analysis_rules": {
                "tie_handling": tie_handling,
                "block_weighting": "equal",
                "bootstrap_seed": 17,
                "bootstrap_resamples": int(
                    supplied_facts.get("bootstrap_resamples", 20_000)
                ),
                "confidence_levels": [0.95],
                "missingness_treatment": supplied_facts.get(
                    "missing_data_rule", "report"
                ),
                "pass_rule": "all-required-gates",
                "downgrade_rule": "limitations",
                "stop_rule": "integrity-failure",
                "scope_narrowing_rule": "material-segment-only",
            },
            "eligibility_thresholds": {
                "minimum_blocks": max(1, len(block_ids)),
                "minimum_coverage": 1.0,
            },
            "segment_rules": {
                "materiality_threshold": 0.1,
                "rule": "evaluate-material-segments",
            },
            "multiplicity_rules": {
                "family_id": str(
                    supplied_facts.get("family_id", f"family-{registration_id}")
                ),
                "family_alpha": 0.05,
                "member_registration_ids": [registration_id],
                "correction_method": "holm",
            },
            "interim_analysis_rules": {
                "allowed": False,
                "maximum_looks": 1,
            },
            "study_design_power": {
                "design_status": str(
                    supplied_facts.get("power_design_status", "approved")
                ),
                "method": str(
                    supplied_facts.get(
                        "power_method",
                        "preregistered-randomized-power-analysis-v1",
                    )
                ),
                "smallest_effect_of_interest": metric.get(
                    "smallest_effect_of_interest"
                ),
                "documented_power": supplied_facts.get(
                    "documented_power", 0.80
                ),
                "evidence_sha256": "sha256:" + "0" * 64,
                "approval_sha256": "sha256:" + "0" * 64,
            },
            "segment_inventory": segments,
            "approval": {
                "approved_at": approved_at,
                "approved_by": supplied_facts.get("approved_by"),
                "authority_root_sha256": "sha256:" + "0" * 64,
                "authority_index_sha256": "sha256:" + "0" * 64,
                "design_evidence_sha256": "sha256:" + "0" * 64,
                "approval_sha256": "sha256:" + "0" * 64,
            },
            "registration_sha256": None,
        }
        supplied_surface = supplied_facts.get("synthetic_surface")
        if isinstance(supplied_surface, Mapping):
            document["synthetic_surface"] = deepcopy(dict(supplied_surface))
    template_surface = document.get("synthetic_surface")
    if not isinstance(template_surface, Mapping):
        raise ContractError("preregistration template synthetic_surface is invalid")
    surface = _surface(run, template_surface)
    document["panel_binding"] = deepcopy(dict(panel_binding))
    document["synthetic_surface"] = surface
    document["registered_by"] = supplied_facts.get(
        "registered_by", document.get("registered_by")
    )
    document["registered_at"] = supplied_facts.get(
        "registered_at", document.get("registered_at")
    )
    document["registration_sha256"] = None

    registration_id = _nonempty(
        document.get("registration_id"), "registration_id"
    )
    claim_scope = document.get("claim_scope")
    if not isinstance(claim_scope, Mapping):
        raise ContractError("preregistration template claim_scope is invalid")
    claim_scope = deepcopy(dict(claim_scope))
    claim_scope["panel_binding"] = deepcopy(dict(panel_binding))
    claim_scope["synthetic_binding"] = {
        "surface": surface["surface"],
        "run_id": surface["run_id"],
        "result_sha256": surface["result_sha256"],
    }
    document["claim_scope"] = claim_scope

    primary = supplied_facts.get("primary_metric")
    if isinstance(primary, Mapping):
        metric = deepcopy(dict(primary))
    else:
        template_metric = document.get("primary_metric")
        metric = (
            deepcopy(dict(template_metric))
            if isinstance(template_metric, Mapping)
            else {}
        )
        if primary is not None:
            metric["name"] = primary
    metric["direction"] = supplied_facts.get(
        "metric_direction", metric.get("direction")
    )
    metric["measurement_window"] = supplied_facts.get(
        "measurement_window", metric.get("measurement_window")
    )
    metric["attribution_window"] = supplied_facts.get(
        "attribution_window", metric.get("attribution_window")
    )
    metric["smallest_effect_of_interest"] = supplied_facts.get(
        "minimum_effect", metric.get("smallest_effect_of_interest")
    )
    document["primary_metric"] = metric
    document["validation_blocks"] = deepcopy(
        supplied_facts.get(
            "validation_blocks", document.get("validation_blocks")
        )
    )
    analysis = deepcopy(dict(document.get("analysis_rules", {})))
    analysis["missingness_treatment"] = supplied_facts.get(
        "missing_data_rule", analysis.get("missingness_treatment")
    )
    document["analysis_rules"] = analysis
    power = deepcopy(dict(document.get("study_design_power", {})))
    power["smallest_effect_of_interest"] = metric.get(
        "smallest_effect_of_interest"
    )
    document["study_design_power"] = power
    approval = deepcopy(dict(document.get("approval", {})))
    approval["approved_by"] = supplied_facts.get(
        "approved_by", approval.get("approved_by")
    )
    approval["approved_at"] = supplied_facts.get(
        "approved_at", approval.get("approved_at")
    )
    document["approval"] = approval
    multiplicity = deepcopy(dict(document.get("multiplicity_rules", {})))
    members = multiplicity.get("member_registration_ids")
    if not isinstance(members, list) or registration_id not in members:
        multiplicity["member_registration_ids"] = [registration_id]
    document["multiplicity_rules"] = multiplicity

    first_access = supplied_facts.get("first_outcome_accessed_at")
    if first_access is not None:
        document["prior_outcome_access"] = [
            {
                "access_sha256": sha256_json(
                    {"reported_outcome_accessed_at": first_access}
                ),
                "accessed_at": first_access,
                "kind": "operator-reported-prior-outcome-access",
            }
        ]
    return document


def _delivery_mapping(
    plan: Mapping[str, object],
    *,
    index: int,
    study_id: str,
    creative_hashes: Mapping[str, str],
) -> dict[str, object]:
    creative_id = _nonempty(
        plan.get("creative_id"), f"campaign_plans[{index}].creative_id"
    )
    asset_sha = _digest(
        plan.get("asset_sha256", creative_hashes.get(creative_id)),
        f"campaign_plans[{index}].asset_sha256",
    )
    block_id = _nonempty(
        plan.get("block_id"), f"campaign_plans[{index}].block_id"
    )
    arm_id = _nonempty(
        plan.get("arm_id"), f"campaign_plans[{index}].arm_id"
    )
    mapping_id = plan.get(
        "mapping_id", f"mapping-{study_id}-{block_id}-{arm_id}-{creative_id}"
    )
    return {
        "mapping_id": mapping_id,
        "platform": plan.get("platform"),
        "platform_campaign_id": plan.get("platform_campaign_id"),
        "platform_ad_group_id": plan.get("platform_ad_group_id"),
        "platform_ad_id": plan.get("platform_ad_id"),
        "platform_creative_id": plan.get("platform_creative_id"),
        "block_id": block_id,
        "study_id": study_id,
        "arm_id": arm_id,
        "batch_id": plan.get("batch_id"),
        "segment_ids": deepcopy(plan.get("segment_ids")),
        "creative_id": creative_id,
        "variant_id": plan.get("variant_id", creative_id),
        "asset_sha256": asset_sha,
        "campaign_plan_sha256": sha256_json(dict(plan)),
    }


def _creative_manifest(
    *,
    registration_id: str,
    run: _RunIdentity,
) -> dict[str, object]:
    ranked_ids = [str(item) for item in run.result["ranked_ids"]]  # type: ignore[index]
    rank = {creative_id: index + 1 for index, creative_id in enumerate(ranked_ids)}
    roles = {
        str(row["creative_id"]): str(row["role"])
        for row in run.testing_map
    }
    creatives = [
        {
            "creative_id": creative_id,
            "variant_id": creative_id,
            "asset_sha256": creative_sha,
            "role": roles.get(creative_id, "planned-creative"),
            "predicted_rank": rank.get(creative_id, len(rank) + 1),
            "predicted_group": rank.get(creative_id, len(rank) + 1),
        }
        for creative_id, creative_sha in run.creative_hashes
    ]
    return validate_creative_manifest(
        {
            "schema_version": CREATIVE_MANIFEST_VERSION,
            "registration_id": registration_id,
            "creatives": creatives,
            "creative_manifest_sha256": None,
        }
    )


def _summary(
    *,
    preregistration: Mapping[str, object],
    delivery_map: Mapping[str, object],
    creative_manifest: Mapping[str, object],
    evidence_status: str,
    unresolved: Sequence[str],
) -> str:
    metric = preregistration.get("primary_metric")
    metric_name = (
        metric.get("name") if isinstance(metric, Mapping) else "unresolved"
    )
    return (
        "# Outcome study preparation\n\n"
        f"- Registration: `{preregistration.get('registration_id')}`\n"
        f"- Study: `{delivery_map.get('study_id')}`\n"
        f"- Primary metric: `{metric_name}`\n"
        f"- Frozen creatives: {len(creative_manifest.get('creatives', []))}\n"
        f"- Frozen delivery mappings: {len(delivery_map.get('mappings', []))}\n"
        f"- Chronology status: `{evidence_status}`\n"
        f"- Unresolved question codes: "
        f"{', '.join(unresolved) if unresolved else 'none'}\n\n"
        "This package records preparation and identity only. It does not "
        "evaluate outcomes, compute ordering or uncertainty, decide "
        "sufficiency or eligibility, or publish a claim.\n"
    )


def build_registration_draft(
    *,
    run_root: Path,
    panel_package: Path,
    campaign_plans: Sequence[object],
    supplied_facts: Mapping[str, object],
) -> RegistrationDraft:
    if not isinstance(supplied_facts, Mapping):
        raise ContractError("supplied_facts must be an object")
    if (
        not isinstance(campaign_plans, Sequence)
        or isinstance(campaign_plans, (str, bytes))
        or not campaign_plans
    ):
        raise ContractError("campaign_plans must be a non-empty sequence")
    panel_binding = _derive_panel_binding(Path(panel_package))
    evidence_root = supplied_facts.get("producer_evidence_root")
    snapshot_root = supplied_facts.get("producer_snapshot_root")
    if not isinstance(evidence_root, (str, Path)) or not isinstance(
        snapshot_root, (str, Path)
    ):
        raise ContractError(
            "authenticated producer evidence and snapshot roots are required"
        )
    run = _authenticate_run(
        Path(run_root),
        evidence_root=Path(evidence_root),
        snapshot_root=Path(snapshot_root),
    )
    plans = tuple(
        _load_campaign_plan(plan, index)
        for index, plan in enumerate(campaign_plans)
    )
    study_ids = {
        _nonempty(plan.get("study_id"), "campaign plan study_id")
        for plan in plans
    }
    if len(study_ids) != 1:
        raise ContractError("campaign plans must bind one study_id")
    study_id = next(iter(study_ids))
    unresolved = tuple(
        code
        for code in _QUESTION_ORDER
        if supplied_facts.get(code) is None
    )
    preregistration = _build_preregistration(
        panel_binding=panel_binding,
        run=run,
        campaign_plans=plans,
        supplied_facts=supplied_facts,
    )
    registration_id = _nonempty(
        preregistration.get("registration_id"), "registration_id"
    )
    creative_hashes = dict(run.creative_hashes)
    mappings = [
        _delivery_mapping(
            plan,
            index=index,
            study_id=study_id,
            creative_hashes=creative_hashes,
        )
        for index, plan in enumerate(plans)
    ]
    chronology = _chronology(
        preregistration=preregistration,
        campaign_plans=plans,
        supplied_facts=supplied_facts,
    )
    allowed_delivery_evidence = frozenset(
        str(mapping["campaign_plan_sha256"]) for mapping in mappings
    )
    status = derive_evidence_status(
        chronology,
        allowed_delivery_evidence_sha256=allowed_delivery_evidence,
    )
    delivery_map = validate_delivery_map(
        {
            "schema_version": DELIVERY_MAP_VERSION,
            "study_id": study_id,
            "registration_id": registration_id,
            "sealed_before_outcome_access": status == "preregistered_holdout",
            "mappings": mappings,
            "chronology": chronology,
            "delivery_map_sha256": None,
        }
    )
    creative_manifest = _creative_manifest(
        registration_id=registration_id, run=run
    )
    return RegistrationDraft(
        preregistration=preregistration,
        delivery_map=delivery_map,
        creative_manifest=creative_manifest,
        study_summary=_summary(
            preregistration=preregistration,
            delivery_map=delivery_map,
            creative_manifest=creative_manifest,
            evidence_status=status,
            unresolved=unresolved,
        ),
        evidence_status=status,
        unresolved_questions=unresolved,
    )


def _file_sha256(path: Path, label: str) -> str:
    selected = Path(path)
    try:
        before = selected.lstat()
        if stat.S_ISLNK(before.st_mode) or not stat.S_ISREG(before.st_mode):
            raise OSError("not a regular file")
        raw = selected.read_bytes()
        after = selected.lstat()
    except OSError as exc:
        raise StudyAuthorityError(f"{label} is not readable authority evidence") from exc
    if (
        before.st_dev,
        before.st_ino,
        before.st_size,
        before.st_mtime_ns,
        before.st_ctime_ns,
    ) != (
        after.st_dev,
        after.st_ino,
        after.st_size,
        after.st_mtime_ns,
        after.st_ctime_ns,
    ):
        raise StudyAuthorityError(f"{label} changed while read")
    return sha256_bytes(raw)


def _sealed_chronology(
    chronology: Mapping[str, object],
    registration: Mapping[str, object],
    delivery_map: Mapping[str, object],
) -> dict[str, object]:
    events = chronology.get("events")
    if not isinstance(events, list):
        raise StudyAuthorityError("chronology evidence is invalid")
    checked = [
        deepcopy(dict(event))
        for event in events
        if isinstance(event, Mapping)
        and event.get("event_type")
        not in {"registration_sealed", "delivery_map_sealed"}
    ]
    approval = registration.get("approval")
    if not isinstance(approval, Mapping):
        raise StudyAuthorityError("registration approval is invalid")
    checked.insert(
        0,
        _event(
            "registration_sealed",
            occurred_at=registration["registered_at"],
            evidence_source_sha256=registration["registration_sha256"],
            attested_by=registration["registered_by"],
            attested_at=registration["registered_at"],
            authority_id=approval["approved_by"],
        ),
    )
    map_projection = {
        "study_id": delivery_map.get("study_id"),
        "registration_id": registration.get("registration_id"),
        "mappings": deepcopy(delivery_map.get("mappings")),
    }
    checked.insert(
        1,
        _event(
            "delivery_map_sealed",
            occurred_at=registration["registered_at"],
            evidence_source_sha256=sha256_json(map_projection),
            attested_by=registration["registered_by"],
            attested_at=registration["registered_at"],
            authority_id=approval["approved_by"],
        ),
    )
    return {"events": checked}


def _write_study_directory(
    *,
    output_dir: Path,
    registration: Mapping[str, object],
    delivery_map: Mapping[str, object],
    creative_manifest: Mapping[str, object],
    summary: str,
    receipt: Mapping[str, object],
) -> None:
    target = Path(output_dir)
    parent = target.parent
    parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        raise StudyAuthorityError(f"study output already exists: {target}")
    temporary = Path(
        tempfile.mkdtemp(prefix=f".{target.name}.stage-", dir=parent)
    )
    try:
        files: dict[str, bytes] = {
            "study-registration.json": canonical_json_bytes(registration),
            "delivery-map.json": canonical_json_bytes(delivery_map),
            "creative-manifest.json": canonical_json_bytes(creative_manifest),
            "study-summary.md": summary.encode("utf-8"),
            "registration-receipt.json": canonical_json_bytes(receipt),
        }
        for name, raw in files.items():
            path = temporary / name
            descriptor = os.open(
                path,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                0o600,
            )
            try:
                written = 0
                while written < len(raw):
                    count = os.write(descriptor, raw[written:])
                    if count <= 0:
                        raise OSError("study write made no progress")
                    written += count
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
        directory_fd = os.open(temporary, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
        _rename_directory_no_replace(temporary, target)
    except BaseException:
        shutil.rmtree(temporary, ignore_errors=True)
        raise


def seal_study_registration(
    *,
    draft: RegistrationDraft,
    authority_root: Path,
    authority_index: Path,
    authority_registry: Path,
    authority_secret_file: Path,
    output_dir: Path | None = None,
) -> SealedStudy:
    """Seal preparation records through the unchanged Tier 4 authority."""

    require_approved_runtime("prepare_study")
    if not isinstance(draft, RegistrationDraft):
        raise StudyAuthorityError("registration draft capability is invalid")
    if draft.unresolved_questions:
        raise StudyAuthorityError(
            "cannot seal a study with unresolved questions: "
            + ", ".join(draft.unresolved_questions)
        )
    if draft.evidence_status == "blocked":
        raise StudyAuthorityError("chronology evidence is conflicting or non-monotone")
    try:
        secret = read_protected_authority_secret(Path(authority_secret_file))
        registry = load_trusted_authority_registry(
            Path(authority_registry), authority_secret=secret
        )
        approved, capability = approve_preregistration_design(
            draft.preregistration,
            authority_registry=registry,
            authority_id=str(draft.preregistration["registered_by"]),
        )
        approval = approved["approval"]
        if not isinstance(approval, Mapping):
            raise StudyAuthorityError("trusted approval is malformed")
        if (
            approval["authority_root_sha256"]
            != _file_sha256(Path(authority_root), "authority root")
            or approval["authority_index_sha256"]
            != _file_sha256(Path(authority_index), "authority index")
        ):
            raise StudyAuthorityError(
                "trusted authority root/index bytes do not match the registry"
            )
        registration = seal_preregistration(
            approved, design_approval=capability
        )
        registration = validate_preregistration(registration)
    except StudyAuthorityError:
        raise
    except (ContractError, KeyError, TypeError, ValueError) as exc:
        raise StudyAuthorityError("study registration authority rejected the design") from exc

    delivery_map_input = deepcopy(draft.delivery_map)
    delivery_map_input["chronology"] = _sealed_chronology(
        delivery_map_input["chronology"],  # type: ignore[arg-type]
        registration,
        delivery_map_input,
    )
    allowed_delivery_evidence = frozenset(
        str(mapping["campaign_plan_sha256"])
        for mapping in delivery_map_input["mappings"]  # type: ignore[union-attr]
        if isinstance(mapping, Mapping)
    )
    status = derive_evidence_status(
        delivery_map_input["chronology"],  # type: ignore[arg-type]
        allowed_delivery_evidence_sha256=allowed_delivery_evidence,
    )
    if status == "blocked":
        raise StudyAuthorityError("chronology evidence is conflicting or non-monotone")
    delivery_map_input["sealed_before_outcome_access"] = (
        status == "preregistered_holdout"
    )
    delivery_map_input["delivery_map_sha256"] = None
    try:
        delivery_map = validate_delivery_map(delivery_map_input)
        creative_manifest = validate_creative_manifest(draft.creative_manifest)
    except ContractError as exc:
        raise StudyAuthorityError("study identity documents failed validation") from exc
    projection = study_receipt_projection(
        registration=registration,
        delivery_map=delivery_map,
        creative_manifest=creative_manifest,
        chronology=delivery_map["chronology"],  # type: ignore[arg-type]
    )
    receipt_input = {
        **projection,
        "receipt_sha256": None,
        "receipt_hmac_sha256": authority_hmac(
            domain=STUDY_RECEIPT_DOMAIN,
            payload=projection,
            secret=secret,
        ),
    }
    receipt_input["receipt_sha256"] = sha256_json(
        {
            **receipt_input,
            "receipt_sha256": None,
            "receipt_hmac_sha256": None,
        }
    )
    receipt = validate_authenticated_registration_receipt(receipt_input)
    study_root = (
        Path(output_dir)
        if output_dir is not None
        else Path.cwd() / f"{delivery_map['study_id']}-outcome-study"
    )
    try:
        _write_study_directory(
            output_dir=study_root,
            registration=registration,
            delivery_map=delivery_map,
            creative_manifest=creative_manifest,
            summary=_summary(
                preregistration=registration,
                delivery_map=delivery_map,
                creative_manifest=creative_manifest,
                evidence_status=status,
                unresolved=(),
            ),
            receipt=receipt,
        )
    except StudyAuthorityError:
        raise
    except (ContractError, OSError, ValueError) as exc:
        raise StudyAuthorityError("study output publication failed") from exc
    return SealedStudy(
        study_root=study_root,
        registration_sha256=str(registration["registration_sha256"]),
        delivery_map_sha256=str(delivery_map["delivery_map_sha256"]),
        creative_manifest_sha256=str(
            creative_manifest["creative_manifest_sha256"]
        ),
        receipt_sha256=str(receipt["receipt_sha256"]),
        evidence_status=status,
    )


__all__ = [
    "REQUIRED_QUESTION_CODES",
    "RegistrationDraft",
    "SealedStudy",
    "build_registration_draft",
    "seal_study_registration",
]
