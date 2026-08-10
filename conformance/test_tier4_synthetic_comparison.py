from __future__ import annotations

from contextlib import contextmanager
from copy import deepcopy
import hashlib
import inspect
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import zipfile


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "skills" / "audience-panel-builder" / "scripts"))

from audience_panel_builder.common import (  # noqa: E402
    ContractError,
    canonical_json_bytes,
    sha256_json,
)
from audience_panel_builder.population.validation.contracts import (  # noqa: E402
    project_shared_outcome_evidence,
    validate_comparison,
)
from audience_panel_builder.population.validation.synthetic import (  # noqa: E402
    _JsonCounters,
    _JsonLimits,
    _canonical_identity,
    _finite_json,
    _ordered_groups,
    FrozenOrdering,
    build_synthetic_outcome_comparison,
    derive_pair_directions,
    load_frozen_ordering,
)
from audience_panel_builder.population.validation import synthetic  # noqa: E402
from audience_panel_builder.population.validation.evidence_snapshot import (  # noqa: E402
    create_evidence_snapshot,
)
from audience_panel_builder.population.validation.evidence_errors import (  # noqa: E402
    ProducerEvidenceError,
)
from audience_panel_builder.population.validation.producer_evidence import (  # noqa: E402
    _publish_revocation,
    _publish_receipt,
    _receipt_name,
    _revocation_name,
    verify_synthetic_producer,
)
from audience_panel_builder.population.validation import producer_evidence  # noqa: E402
from audience_panel_builder.population.validation.replay_inputs import (  # noqa: E402
    ProducerReplayInputs,
)
from conformance.test_tier4_validation_contracts import (  # noqa: E402
    approved_seal,
    digest,
    observation_fixture,
    preregistration_fixture,
    tie_handling,
)
from conformance.test_tier4_synthetic_producer_evidence import (  # noqa: E402
    valid_record,
)


SURFACES = (
    "complete_exposure_ordering",
    "maxdiff_screening_ordering",
    "pairwise_boundary_ordering",
)


def seal_preregistration(payload: object) -> dict[str, object]:
    """Finalize this producer fixture's exact arm/segment design before approval."""
    registration = deepcopy(payload)
    assert isinstance(registration, dict)
    segments = registration["segment_inventory"]
    assert isinstance(segments, list)
    for block in registration["validation_blocks"]:
        segment_ids = [
            segment["segment_id"] for segment in segments
            if block["block_id"] in segment["planned_block_ids"]
        ]
        block["planned_segment_membership"] = [{
            "arm_id": arm_id,
            "segment_ids": segment_ids,
        } for arm_id in sorted(block["planned_arm_ids"])]
    return approved_seal(registration)


def raw_digest(value: bytes) -> str:
    return "sha256:" + hashlib.sha256(value).hexdigest()


def binding(path: str, canonical_digest: str, *, raw: str | None = None, count=None):
    return {
        "path": path,
        "raw_bytes_sha256": raw or canonical_digest,
        "canonical_document_sha256": canonical_digest,
        "record_count": count,
    }


def reseal_observation(document: dict[str, object]) -> None:
    shared = project_shared_outcome_evidence(document)
    document["shared_outcome_evidence_binding"] = {
        "shared_evidence_id": shared["shared_evidence_id"],
        "study_id": shared["study_id"],
        "shared_evidence_sha256": shared["shared_evidence_sha256"],
    }
    document["observation_sha256"] = sha256_json({
        **document, "observation_sha256": None,
    })


def result_fixture(
    surface: str,
    *,
    ranked: tuple[str, ...] = ("creative-a", "creative-b", "creative-c"),
    utilities: dict[str, float] | None = None,
) -> dict[str, object]:
    values = utilities or {
        creative_id: float(len(ranked) - index)
        for index, creative_id in enumerate(ranked)
    }
    common = {
        "study_id": "run-1",
        "estimand": "protocol-relative",
        "stability_diagnostic": "conditional",
        "utilities": values,
        "ranked_ids": list(ranked),
        "classifications": {creative_id: "boundary_candidate" for creative_id in ranked},
        "model_diagnostics": {},
        "interpretation_limits": [],
    }
    if surface == "complete_exposure_ordering":
        return {
            **common,
            "method": "complete_exposure",
            "requested_top_k": 1,
            "top_k_inclusion_frequencies": {creative_id: 0.5 for creative_id in ranked},
            "selection_status": "resolved",
            "proposed_finalist_ids": [ranked[0]],
            "archetype_sensitivity": {},
            "recovery_config_version": "complete-exposure-calibration-v2",
            "validity_status": "valid",
            "validity_reasons": [],
        }
    if surface == "maxdiff_screening_ordering":
        return {
            **common,
            "method": "partial_exposure_maxdiff",
            "requested_top_k": 1,
            "top_k_inclusion_frequencies": {creative_id: 0.5 for creative_id in ranked},
            "selection_status": "boundary_required",
            "proposed_finalist_ids": [],
            "archetype_sensitivity": {},
            "recovery_config_version": "recovery-v1",
            "validity_status": "valid",
            "validity_reasons": [],
            "boundary_plan": {},
        }
    return {
        **common,
        "status": "resolved",
        "status_reasons": [],
        "boundary_candidate_ids": list(ranked),
        "frozen_clear_finalist_ids": [],
        "frozen_clear_non_finalist_ids": [],
        "selected_boundary_ids": [ranked[0]],
        "proposed_finalist_ids": [ranked[0]],
        "conditional_inclusion_frequencies": {
            creative_id: 0.5 for creative_id in ranked
        },
        "decision_audit": {},
    }


class AuthenticatedFixture:
    def __init__(
        self,
        case: unittest.TestCase,
        *,
        surface: str = "complete_exposure_ordering",
        ranked: tuple[str, ...] = ("creative-a", "creative-b", "creative-c"),
        full_roster: tuple[str, ...] | None = None,
        utilities: dict[str, float] | None = None,
        result: dict[str, object] | None = None,
        tolerance: float = 0.001,
        temporary_parent: Path | None = None,
    ):
        temporary = tempfile.TemporaryDirectory(dir=temporary_parent)
        case.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name)
        self.evidence_root = self.base / "evidence"
        self.snapshot_root = self.base / "snapshots"
        self.evidence_root.mkdir()
        self.snapshot_root.mkdir()
        self.surface = surface
        self.result = result or result_fixture(
            surface, ranked=ranked, utilities=utilities,
        )
        self.result_raw = (
            json.dumps(self.result, indent=2, sort_keys=True).encode() + b"\n"
        )
        self.result_path = self.base / (
            "boundary-results.json"
            if surface == "pairwise_boundary_ordering"
            else "screening-model-results.json"
        )
        self.result_path.write_bytes(self.result_raw)
        self.result_path.chmod(0o400)
        roster = full_roster or ranked
        self.creative_hashes = {
            creative_id: digest(str(index + 1))
            for index, creative_id in enumerate(sorted(roster))
        }
        self.manifest = {
            "study_id": "run-1",
            "outputs": {"creative_asset_hashes": self.creative_hashes},
        }
        self.manifest_path = self.base / "study-manifest.json"
        self.manifest_path.write_bytes(canonical_json_bytes(self.manifest))
        self.manifest_path.chmod(0o400)
        self.result_sha256 = sha256_json(self.result)
        manifest_sha = sha256_json(self.manifest)
        inputs = {
            "study_manifest": binding("study-manifest.json", manifest_sha),
        }
        for index, role in enumerate((
            "accepted_responses", "raw_provider_returns",
            "rejected_attempts", "dispatch_audit",
        )):
            inputs[role] = binding(
                f"{role}.jsonl", digest(chr(97 + index)), count=1,
            )
        method = (
            "complete_exposure"
            if surface == "complete_exposure_ordering"
            else "partial_exposure_maxdiff"
        )
        stage = "boundary" if surface == "pairwise_boundary_ordering" else "screening"
        if surface == "complete_exposure_ordering":
            policy = {
                "ordering_equivalence": "exact-utility-equality-v1",
                "ordering_tiebreak": "creative-id-serialization-only-v1",
            }
        else:
            policy = {
                "ordering_equivalence": "rounded-utility-bucket-v1",
                "ordering_tiebreak": "creative-id-serialization-only-v1",
                "effective_ordering_tolerance": tolerance,
                "rounding_rule": "python-half-even-v1",
            }
        semantics = {
            "policy_bindings": policy,
            "producer_semantics_sha256": digest("8"),
        }
        self.receipt = {
            "surface": surface,
            "method": method,
            "stage": stage,
            "run_id": "run-1",
            "frozen_at": "2026-07-30T00:00:00Z",
            "sealed_at": "2026-07-31T00:00:00Z",
            "producer_semantics": semantics,
            "input_bindings": inputs,
            "result_binding": binding(
                self.result_path.name,
                self.result_sha256,
                raw=raw_digest(self.result_raw),
            ),
            "snapshot_binding": {
                "snapshot_id": "snapshot-1",
                "snapshot_sha256": digest("b"),
                "archive_sha256": digest("c"),
            },
            "producer_evidence_sha256": digest("7"),
        }
        registration = preregistration_fixture()
        registration["synthetic_surface"] = {
            "surface": surface,
            "method": method,
            "stage": stage,
            "run_id": "run-1",
            "result_path": self.result_path.name,
            "result_sha256": self.result_sha256,
            "result_bytes_sha256": raw_digest(self.result_raw),
            "manifest_sha256": manifest_sha,
            "lineage_bundle_sha256": synthetic.lineage_bundle_sha256({
                role: inputs[role]
                for role in synthetic.LINEAGE_ORDER
            }),
            "producer_evidence_sha256": digest("7"),
            "producer_semantics_sha256": digest("8"),
            "frozen_at": "2026-07-30T00:00:00Z",
            "producer_evidence_sealed_at": "2026-07-31T00:00:00Z",
            "eligible_creatives": [
                {
                    "creative_id": creative_id,
                    "creative_sha256": creative_hash,
                }
                for creative_id, creative_hash in sorted(
                    self.creative_hashes.items()
                )
            ],
        }
        compact = {
            "surface": surface,
            "run_id": "run-1",
            "result_sha256": self.result_sha256,
        }
        registration["claim_scope"]["synthetic_binding"] = compact
        registration["analysis_rules"]["tie_handling"] = (
            tie_handling(surface)
            if surface == "complete_exposure_ordering"
            else policy
        )
        registration["validation_blocks"][0]["planned_arm_ids"] = [
            f"arm-{creative_id.removeprefix('creative-')}"
            for creative_id in ranked
        ]
        registration["registration_sha256"] = None
        self.registration = seal_preregistration(registration)

    @contextmanager
    def open_snapshot(self, **kwargs):
        del kwargs
        receipt = self.receipt
        manifest_path = self.manifest_path
        result_path = self.result_path

        class Snapshot:
            snapshot_id = receipt["snapshot_binding"]["snapshot_id"]
            snapshot_sha256 = receipt["snapshot_binding"]["snapshot_sha256"]
            archive_sha256 = receipt["snapshot_binding"]["archive_sha256"]
            frozen_at = receipt["frozen_at"]

            @staticmethod
            def resolve_member(role: str) -> Path:
                if role == "study_manifest":
                    return manifest_path
                if role == "result":
                    return result_path
                raise AssertionError(role)

        yield Snapshot()

    def load(self) -> FrozenOrdering:
        with patch.object(
            synthetic,
            "validate_synthetic_producer_evidence",
            return_value=deepcopy(self.receipt),
        ) as validate, patch.object(
            synthetic, "open_evidence_snapshot", self.open_snapshot,
        ):
            ordering = load_frozen_ordering(
                surface=self.surface,
                result=deepcopy(self.result),
                registration=deepcopy(self.registration),
                evidence_root=self.evidence_root,
                snapshot_root=self.snapshot_root,
            )
        validate.assert_called_once_with(
            surface=self.surface,
            run_id="run-1",
            result_sha256=self.result_sha256,
            evidence_root=self.evidence_root,
            snapshot_root=self.snapshot_root,
        )
        return ordering

    def compare(
        self, observations: list[dict[str, object]] | None = None,
    ) -> dict[str, object]:
        observations = observations or self.observations()
        with patch.object(
            synthetic,
            "validate_synthetic_producer_evidence",
            return_value=deepcopy(self.receipt),
        ), patch.object(
            synthetic, "open_evidence_snapshot", self.open_snapshot,
        ):
            return build_synthetic_outcome_comparison(
                registration=deepcopy(self.registration),
                result=deepcopy(self.result),
                evidence_root=self.evidence_root,
                snapshot_root=self.snapshot_root,
                observations=observations,
            )

    def observations(
        self, successes: tuple[int, ...] | None = None,
    ) -> list[dict[str, object]]:
        ranked = tuple(self.result["ranked_ids"])
        successes = successes or tuple(
            80 - index * 20 for index in range(len(ranked))
        )
        documents = []
        for creative_id, success in zip(ranked, successes):
            suffix = creative_id.removeprefix("creative-")
            document = observation_fixture()
            registration_binding = document["registration_binding"]
            assert isinstance(registration_binding, dict)
            registration_binding.update({
                "registration_id": self.registration["registration_id"],
                "registration_sha256": self.registration["registration_sha256"],
                "registered_at": self.registration["registered_at"],
                "status": self.registration["status"],
                "holdout_partition": self.registration["holdout_partition"],
                "claim_scope": self.registration["claim_scope"],
                "multiplicity_rules": self.registration["multiplicity_rules"],
                "preregistration": self.registration,
            })
            document["synthetic_binding"] = {
                "surface": self.surface,
                "run_id": "run-1",
                "result_sha256": self.result_sha256,
            }
            document["panel_binding"] = self.registration["panel_binding"]
            document["claim_scope"] = self.registration["claim_scope"]
            document["block_id"] = "campaign-q3"
            document["arm_id"] = f"arm-{suffix}"
            document["observation_id"] = f"observation-{suffix}"
            document["creative_binding"] = {
                "creative_id": creative_id,
                "creative_sha256": self.creative_hashes[creative_id],
            }
            document["aggregate"] = {
                "success_count": success,
                "eligible_exposure_count": 100,
            }
            shared = project_shared_outcome_evidence(document)
            document["shared_outcome_evidence_binding"] = {
                "shared_evidence_id": shared["shared_evidence_id"],
                "study_id": shared["study_id"],
                "shared_evidence_sha256": shared["shared_evidence_sha256"],
            }
            document["observation_sha256"] = sha256_json({
                **document, "observation_sha256": None,
            })
            documents.append(document)
        return documents


class RealProducerWorld:
    """One real producer/receipt/snapshot world shared by authority tests."""

    def __init__(self, case: unittest.TestCase):
        from conformance.test_task9_integration import (
            complete_calibration_policy,
            complete_job,
            complete_manifest,
            complete_response,
        )
        from conformance.test_task9_review_fixes_wave2 import (
            _bind_without_semantic_validation,
            _raw_for_response,
            _rejected_from_raw,
        )
        from conformance.test_tier4_replay_inputs import _accepted_workflow
        from conformance.test_maxdiff import (
            full_job_for_response,
            full_response_for_block,
            matching_manifest as maxdiff_manifest,
            recovery_config,
        )
        from conformance.test_pairwise import (
            boundary_fixture,
            matching_manifest as pairwise_manifest,
        )

        temporary = tempfile.TemporaryDirectory()
        case.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name)
        self.evidence_root = self.base / "evidence"
        self.snapshot_root = self.base / "snapshots"
        self.evidence_root.mkdir()
        self.snapshot_root.mkdir()
        self.surfaces: dict[str, dict[str, object]] = {}

        complete_root = self.base / "complete"
        complete_root.mkdir()
        manifest = complete_manifest()
        responses = [
            complete_response(
                index,
                ["creative-a", "creative-b", "creative-c", "creative-d"],
            )
            for index in range(1, 10)
        ]
        accepted = responses[0]["runtime_attempts"][0]
        accepted["attempt_number"] = 2
        accepted["attempt_id"] = accepted["provider_return_id"] = (
            accepted["provider_return_id"].replace("-a1-", "-a2-")
        )
        responses[0]["per_creative_reactions"][0][
            "source_provenance"
        ]["provider_return_id"] = accepted["provider_return_id"]
        responses[0]["runtime_attempts"].insert(0, {
            "attempt_id": "raw-S1-0001-r1-a1-ce-01",
            "stage": "reaction",
            "position_seen": 1,
            "attempt_number": 1,
            "provider_return_id": "raw-S1-0001-r1-a1-ce-01",
            "outcome": "rejected",
            "validation_errors": ["schema mismatch"],
        })
        raw_returns = [
            raw for response in responses for raw in _raw_for_response(response)
        ]
        contract = {
            "retry_limit_per_return": 1,
            "reaction_positions": [1, 2, 3, 4],
            "comparison_required": True,
        }
        workflow = {
            "status": "complete",
            "responses": responses,
            "raw_provider_returns": raw_returns,
            "rejected_attempts": [_rejected_from_raw(raw_returns[0])],
            "dispatch_audit": [{
                "record_type": response["record_type"],
                "synthetic_replicate_id": response[
                    "synthetic_replicate_id"
                ],
                "reviewer_dispatch_id": response["reviewer_dispatch_id"],
                "accepted": True,
                "attempt_contract": contract,
                "reaction_attempts": (
                    [2, 1, 1, 1] if index == 0 else [1, 1, 1, 1]
                ),
                "comparison_attempts": 1,
            } for index, response in enumerate(responses)],
            "requested_replicates": 9,
            "completed_replicates": 9,
        }
        run_dir, *_ = _bind_without_semantic_validation(
            complete_root, manifest, workflow,
        )
        jobs = complete_root / "screening-jobs.json"
        jobs.write_bytes(canonical_json_bytes({
            "study_id": manifest["study_id"],
            "method": "complete_exposure",
            "record_type": "screening_response",
            "synthetic_replicate_jobs": [
                complete_job(response) for response in responses
            ],
        }))
        recovery = complete_root / "recovery.json"
        recovery.write_bytes(
            canonical_json_bytes(complete_calibration_policy())
        )
        projection = complete_root / "screening-response-projection.jsonl"
        projection.write_bytes(
            b"".join(canonical_json_bytes(response) for response in responses)
        )
        complete_result = complete_root / "screening-model-results.json"
        self._run([
            sys.executable,
            str(ROOT / "skills/audience-ad-testing-lab/scripts/aggregate-screening.py"),
            "screening",
            "--manifest", str(run_dir / "study-manifest.json"),
            "--jobs", str(jobs),
            "--responses", str(projection),
            "--recovery-config", str(recovery),
            "--output", str(complete_result),
        ])
        complete_inputs = ProducerReplayInputs(
            study_manifest=run_dir / "study-manifest.json",
            accepted_responses=run_dir / "panelist-responses.jsonl",
            raw_provider_returns=run_dir / "raw-provider-returns.jsonl",
            rejected_attempts=run_dir / "rejected-attempts.jsonl",
            cumulative_dispatch_audit=run_dir / "dispatch-audit.jsonl",
            result=complete_result,
            screening_jobs=jobs,
            recovery_configuration=recovery,
            command_dispatch_audit_input=None,
            screening_result=None,
            screening_producer_evidence=None,
        )
        complete_record = verify_synthetic_producer(
            surface="complete_exposure_ordering",
            inputs=complete_inputs,
            allowed_source_roots=[complete_root],
            runtime_root=ROOT,
            snapshot_root=self.snapshot_root,
            evidence_root=self.evidence_root,
        )
        self._remember(
            "complete_exposure_ordering",
            complete_record,
            complete_result,
            run_dir / "study-manifest.json",
        )

        max_root = self.base / "maxdiff"
        max_root.mkdir()
        run_id = "screening-acme-q3-001"
        max_responses = [
            full_response_for_block(
                ["v1", "v2", "v3", "v4"], index, study_id=run_id,
            )
            for index in range(1, 13)
        ]
        for index, response in enumerate(max_responses, 1):
            response["persona_archetype_id"] = f"A{((index - 1) // 4) + 1}"
            ids = ["v1", "v2", "v3", "v4"]
            choice = response["comparative_choice"]
            choice["best_variation_id"] = ids[(index - 1) % 4]
            choice["weakest_variation_id"] = ids[(index + 1) % 4]
            provider_ids = {}
            for attempt in response["runtime_attempts"]:
                old = attempt["provider_return_id"]
                new = f"{old}-{index:02d}"
                provider_ids[old] = new
                attempt["provider_return_id"] = new
                attempt["attempt_id"] = f"{attempt['attempt_id']}-{index:02d}"
            for reaction in response["per_creative_reactions"]:
                provenance = reaction["source_provenance"]
                provenance["provider_return_id"] = provider_ids[
                    provenance["provider_return_id"]
                ]
            provenance = choice["source_provenance"]
            provenance["provider_return_id"] = provider_ids[
                provenance["provider_return_id"]
            ]
        max_manifest = maxdiff_manifest(
            study_id=run_id,
            creative_ids=("v1", "v2", "v3", "v4"),
        )
        max_manifest["outputs"]["creative_asset_hashes"] = {
            creative_id: digest(str(index))
            for index, creative_id in enumerate(
                ("v1", "v2", "v3", "v4"), 1,
            )
        }
        capacity = max_manifest["synthetic_replicate_capacity"]
        capacity["screening_planned"] = len(max_responses) + 1
        max_manifest["maximum_synthetic_panelists"] = (
            len(max_responses) + 1
            + capacity["boundary_reserved"] + capacity["finalist_reserved"]
        )
        max_run, *_ = _bind_without_semantic_validation(
            max_root, max_manifest, _accepted_workflow(max_responses),
        )
        planned_jobs = [
            full_job_for_response(response) for response in max_responses
        ]
        exhausted = deepcopy(planned_jobs[0])
        exhausted.update({
            "response_id": "response-exhausted-authorized",
            "synthetic_replicate_id": "replicate-exhausted-authorized",
            "dispatch_id": "dispatch-exhausted-authorized",
        })
        planned_jobs.append(exhausted)
        max_jobs = max_root / "screening-jobs.json"
        max_jobs.write_bytes(canonical_json_bytes({
            "study_id": run_id,
            "method": "partial_exposure_maxdiff",
            "record_type": "screening_response",
            "synthetic_replicate_jobs": planned_jobs,
        }))
        config = recovery_config()
        config["calibration_status"] = "calibrated"
        config["library_size_bands"] = [{
            "name": "small_calibrated_library",
            "minimum": 4, "maximum": 10,
        }]
        config["shortlist_size_bands"] = [{
            "name": "small_calibrated_shortlist",
            "minimum": 2, "maximum": 3,
        }]
        config["utility_separation_band"]["maximum_log_utility_gap"] = 100.0
        max_recovery = max_root / "recovery.json"
        max_recovery.write_bytes(canonical_json_bytes(config))
        max_result = max_root / "screening-model-results.json"
        command_audit = max_root / "command-dispatch-audit.jsonl"
        command_audit.write_bytes((max_run / "dispatch-audit.jsonl").read_bytes())
        max_inputs = ProducerReplayInputs(
            study_manifest=max_run / "study-manifest.json",
            accepted_responses=max_run / "panelist-responses.jsonl",
            raw_provider_returns=max_run / "raw-provider-returns.jsonl",
            rejected_attempts=max_run / "rejected-attempts.jsonl",
            cumulative_dispatch_audit=max_run / "dispatch-audit.jsonl",
            result=max_result,
            screening_jobs=max_jobs,
            recovery_configuration=max_recovery,
            command_dispatch_audit_input=command_audit,
            screening_result=None,
            screening_producer_evidence=None,
        )
        self._run([
            sys.executable,
            str(ROOT / "skills/audience-ad-testing-lab/scripts/aggregate-screening.py"),
            "screening",
            "--manifest", str(max_inputs.study_manifest),
            "--jobs", str(max_inputs.screening_jobs),
            "--responses", str(max_inputs.accepted_responses),
            "--dispatch-audit", str(command_audit),
            "--recovery-config", str(max_recovery),
            "--output", str(max_result),
        ])
        max_record = verify_synthetic_producer(
            surface="maxdiff_screening_ordering",
            inputs=max_inputs,
            allowed_source_roots=[ROOT, max_root],
            runtime_root=ROOT,
            snapshot_root=self.snapshot_root,
            evidence_root=self.evidence_root,
        )
        self._remember(
            "maxdiff_screening_ordering",
            max_record,
            max_result,
            max_run / "study-manifest.json",
        )

        pair_root = self.base / "pairwise"
        pair_root.mkdir()
        screening = json.loads(max_result.read_bytes())
        assignments = screening["boundary_plan"]["predeclared_pair_assignments"]
        pair_responses = []
        templates = boundary_fixture()
        # The locked fixture reaches a valid, resolved stop after wave two.
        # Later predeclared responses must remain undispatched after that stop.
        for index, assignment in enumerate(assignments[:8], 1):
            response = deepcopy(templates[(index - 1) % len(templates)])
            replacements = dict(zip(
                response["assigned_variation_ids"],
                assignment["variation_ids"],
                strict=True,
            ))
            response.update({
                "study_id": run_id,
                "response_id": f"boundary-response-{index:02d}",
                "synthetic_replicate_id": f"boundary-replicate-{index:02d}",
                "reviewer_dispatch_id": f"boundary-dispatch-{index:02d}",
                "assigned_variation_ids": list(assignment["variation_ids"]),
                "shown_order": [
                    replacements[item] for item in response["shown_order"]
                ],
                "blind_labels": {
                    replacements[item]: label
                    for item, label in response["blind_labels"].items()
                },
                "pair_assignment_id": assignment["pair_assignment_id"],
                "boundary_wave": assignment["wave"],
            })
            provider_ids = {}
            for attempt in response["runtime_attempts"]:
                old = attempt["provider_return_id"]
                new = f"{old}-{index:02d}"
                provider_ids[old] = new
                attempt["provider_return_id"] = new
                attempt["attempt_id"] = f"{attempt['attempt_id']}-{index:02d}"
            for reaction in response["per_creative_reactions"]:
                reaction["variation_id"] = replacements[
                    reaction["variation_id"]
                ]
                reaction["reaction_id"] += f"-{index:02d}"
                provenance = reaction["source_provenance"]
                provenance["provider_return_id"] = provider_ids[
                    provenance["provider_return_id"]
                ]
            choice = response["pairwise_choice"]
            choice["status"] = "first_preferred"
            choice["preferred_variation_id"] = response["shown_order"][0]
            choice["reason"] = "The first shown creative is more persuasive."
            provenance = choice["source_provenance"]
            provenance["provider_return_id"] = provider_ids[
                provenance["provider_return_id"]
            ]
            choice["frozen_reaction_ids"] = [
                item["reaction_id"]
                for item in response["per_creative_reactions"]
            ]
            pair_responses.append(response)
        pair_manifest = pairwise_manifest(
            records=pair_responses,
            creative_ids=("v1", "v2", "v3", "v4"),
            shortlist_size=2,
        )
        pair_manifest["outputs"]["creative_asset_hashes"] = deepcopy(
            max_manifest["outputs"]["creative_asset_hashes"]
        )
        pair_manifest["synthetic_replicate_capacity"] = deepcopy(capacity)
        pair_manifest["maximum_synthetic_panelists"] = max_manifest[
            "maximum_synthetic_panelists"
        ]
        pair_run, *_ = _bind_without_semantic_validation(
            pair_root, pair_manifest, _accepted_workflow(pair_responses),
        )
        pair_screening = pair_root / "screening-model-results.json"
        pair_screening.write_bytes(max_result.read_bytes())
        pair_result = pair_root / "boundary-results.json"
        max_receipt = self.evidence_root / _receipt_name(
            "maxdiff_screening_ordering",
            str(max_record["run_id"]),
            str(max_record["result_binding"]["canonical_document_sha256"]),
        )
        pair_inputs = ProducerReplayInputs(
            study_manifest=pair_run / "study-manifest.json",
            accepted_responses=pair_run / "panelist-responses.jsonl",
            raw_provider_returns=pair_run / "raw-provider-returns.jsonl",
            rejected_attempts=pair_run / "rejected-attempts.jsonl",
            cumulative_dispatch_audit=pair_run / "dispatch-audit.jsonl",
            result=pair_result,
            screening_jobs=None,
            recovery_configuration=None,
            command_dispatch_audit_input=None,
            screening_result=pair_screening,
            screening_producer_evidence=max_receipt,
        )
        self._run([
            sys.executable,
            str(ROOT / "skills/audience-ad-testing-lab/scripts/aggregate-screening.py"),
            "boundary",
            "--manifest", str(pair_inputs.study_manifest),
            "--screening-results", str(pair_inputs.screening_result),
            "--responses", str(pair_inputs.accepted_responses),
            "--output", str(pair_result),
        ])
        pair_record = verify_synthetic_producer(
            surface="pairwise_boundary_ordering",
            inputs=pair_inputs,
            allowed_source_roots=[ROOT, pair_root, self.evidence_root],
            runtime_root=ROOT,
            snapshot_root=self.snapshot_root,
            evidence_root=self.evidence_root,
        )
        self._remember(
            "pairwise_boundary_ordering",
            pair_record,
            pair_result,
            pair_run / "study-manifest.json",
        )

    def _run(self, command: list[str]) -> None:
        completed = subprocess.run(
            command,
            env={
                "LANG": "C", "LC_ALL": "C",
                "PYTHONPATH": str(
                    ROOT / "skills/audience-ad-testing-lab/scripts"
                ),
                "PYTHONDONTWRITEBYTECODE": "1",
            },
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
            timeout=240,
        )
        if completed.returncode:
            output = Path(command[command.index("--output") + 1])
            raise AssertionError((
                command, completed.stdout, completed.stderr,
                output.read_text() if output.exists() else None,
            ))

    def _remember(
        self, surface: str, record: dict[str, object],
        result_path: Path, manifest_path: Path,
    ) -> None:
        self.surfaces[surface] = {
            "record": record,
            "result": json.loads(result_path.read_bytes()),
            "manifest": json.loads(manifest_path.read_bytes()),
        }

    def build_maxdiff_variant(
        self, *, slug: str, best_pattern: tuple[str, ...],
    ) -> dict[str, object]:
        """Run and authenticate an actual unchanged MaxDiff CLI variant."""
        from conformance.test_task9_review_fixes_wave2 import (
            _bind_without_semantic_validation,
        )
        from conformance.test_tier4_replay_inputs import _accepted_workflow
        from conformance.test_maxdiff import (
            full_job_for_response,
            full_response_for_block,
            matching_manifest,
            recovery_config,
        )

        variant_root = self.base / slug
        variant_root.mkdir()
        run_id = f"{slug}-001"
        creative_ids = ("v1", "v2", "v3", "v4")
        responses = [
            full_response_for_block(
                list(creative_ids), index, study_id=run_id,
            )
            for index in range(1, 25)
        ]
        for index, response in enumerate(responses, 1):
            response["persona_archetype_id"] = (
                f"A{((index - 1) // 8) + 1}"
            )
            choice = response["comparative_choice"]
            choice["best_variation_id"] = best_pattern[
                (index - 1) % len(best_pattern)
            ]
            choice["weakest_variation_id"] = (
                "v3" if choice["best_variation_id"] == "v2" else "v4"
            )
            if best_pattern == (
                "v1", "v1", "v2", "v3", "v1", "v1", "v2", "v3",
            ):
                choice["weakest_variation_id"] = "v4"
            provider_ids = {}
            for attempt in response["runtime_attempts"]:
                old = attempt["provider_return_id"]
                new = f"{old}-{index:02d}"
                provider_ids[old] = new
                attempt["provider_return_id"] = new
                attempt["attempt_id"] = (
                    f"{attempt['attempt_id']}-{index:02d}"
                )
            for reaction in response["per_creative_reactions"]:
                provenance = reaction["source_provenance"]
                provenance["provider_return_id"] = provider_ids[
                    provenance["provider_return_id"]
                ]
            provenance = choice["source_provenance"]
            provenance["provider_return_id"] = provider_ids[
                provenance["provider_return_id"]
            ]

        manifest = matching_manifest(
            study_id=run_id, creative_ids=creative_ids,
        )
        manifest["outputs"]["creative_asset_hashes"] = {
            creative_id: digest(str(index))
            for index, creative_id in enumerate(creative_ids, 1)
        }
        capacity = manifest["synthetic_replicate_capacity"]
        capacity["screening_planned"] = len(responses) + 1
        manifest["maximum_synthetic_panelists"] = (
            len(responses) + 1
            + capacity["boundary_reserved"] + capacity["finalist_reserved"]
        )
        run_dir, *_ = _bind_without_semantic_validation(
            variant_root, manifest, _accepted_workflow(responses),
        )
        planned_jobs = [
            full_job_for_response(response) for response in responses
        ]
        exhausted = deepcopy(planned_jobs[0])
        exhausted.update({
            "response_id": "response-exhausted-authorized",
            "synthetic_replicate_id": "replicate-exhausted-authorized",
            "dispatch_id": "dispatch-exhausted-authorized",
        })
        planned_jobs.append(exhausted)
        jobs = variant_root / "screening-jobs.json"
        jobs.write_bytes(canonical_json_bytes({
            "study_id": run_id,
            "method": "partial_exposure_maxdiff",
            "record_type": "screening_response",
            "synthetic_replicate_jobs": planned_jobs,
        }))
        config = recovery_config()
        config["calibration_status"] = "calibrated"
        config["library_size_bands"] = [{
            "name": "small_calibrated_library",
            "minimum": 4, "maximum": 10,
        }]
        config["shortlist_size_bands"] = [{
            "name": "small_calibrated_shortlist",
            "minimum": 2, "maximum": 3,
        }]
        config["utility_separation_band"][
            "maximum_log_utility_gap"
        ] = 100.0
        recovery = variant_root / "recovery.json"
        recovery.write_bytes(canonical_json_bytes(config))
        result_path = variant_root / "screening-model-results.json"
        command_audit = variant_root / "command-dispatch-audit.jsonl"
        command_audit.write_bytes(
            (run_dir / "dispatch-audit.jsonl").read_bytes()
        )
        inputs = ProducerReplayInputs(
            study_manifest=run_dir / "study-manifest.json",
            accepted_responses=run_dir / "panelist-responses.jsonl",
            raw_provider_returns=run_dir / "raw-provider-returns.jsonl",
            rejected_attempts=run_dir / "rejected-attempts.jsonl",
            cumulative_dispatch_audit=run_dir / "dispatch-audit.jsonl",
            result=result_path,
            screening_jobs=jobs,
            recovery_configuration=recovery,
            command_dispatch_audit_input=command_audit,
            screening_result=None,
            screening_producer_evidence=None,
        )
        self._run([
            sys.executable,
            str(
                ROOT
                / "skills/audience-ad-testing-lab/scripts/aggregate-screening.py"
            ),
            "screening",
            "--manifest", str(inputs.study_manifest),
            "--jobs", str(inputs.screening_jobs),
            "--responses", str(inputs.accepted_responses),
            "--dispatch-audit", str(command_audit),
            "--recovery-config", str(inputs.recovery_configuration),
            "--output", str(result_path),
        ])
        record = verify_synthetic_producer(
            surface="maxdiff_screening_ordering",
            inputs=inputs,
            allowed_source_roots=[ROOT, variant_root],
            runtime_root=ROOT,
            snapshot_root=self.snapshot_root,
            evidence_root=self.evidence_root,
        )
        self._remember(
            slug, record, result_path, run_dir / "study-manifest.json",
        )
        return {
            "root": variant_root,
            "run_dir": run_dir,
            "record": record,
            "result_path": result_path,
            "result": json.loads(result_path.read_bytes()),
            "manifest": json.loads(
                (run_dir / "study-manifest.json").read_bytes()
            ),
        }

    def build_two_candidate_pairwise(
        self, maxdiff_world: dict[str, object],
    ) -> str:
        """Authenticate a real recursive pairwise result over two arms."""
        from conformance.test_task9_review_fixes_wave2 import (
            _bind_without_semantic_validation,
        )
        from conformance.test_tier4_replay_inputs import _accepted_workflow
        from conformance.test_pairwise import (
            boundary_fixture,
            matching_manifest,
        )

        screening = maxdiff_world["result"]
        manifest = maxdiff_world["manifest"]
        record = maxdiff_world["record"]
        assert isinstance(screening, dict)
        assert isinstance(manifest, dict)
        assert isinstance(record, dict)
        assignments = screening["boundary_plan"][
            "predeclared_pair_assignments"
        ]
        templates = boundary_fixture()
        responses = []
        for index, assignment in enumerate(assignments[:4], 1):
            response = deepcopy(templates[index - 1])
            replacements = dict(zip(
                response["assigned_variation_ids"],
                assignment["variation_ids"],
                strict=True,
            ))
            response.update({
                "study_id": record["run_id"],
                "response_id": f"two-arm-response-{index:02d}",
                "synthetic_replicate_id": (
                    f"two-arm-replicate-{index:02d}"
                ),
                "reviewer_dispatch_id": (
                    f"two-arm-dispatch-{index:02d}"
                ),
                "assigned_variation_ids": list(
                    assignment["variation_ids"]
                ),
                "shown_order": [
                    replacements[item] for item in response["shown_order"]
                ],
                "blind_labels": {
                    replacements[item]: label
                    for item, label in response["blind_labels"].items()
                },
                "pair_assignment_id": assignment["pair_assignment_id"],
                "boundary_wave": assignment["wave"],
            })
            provider_ids = {}
            for attempt in response["runtime_attempts"]:
                old = attempt["provider_return_id"]
                new = f"{old}-two-{index:02d}"
                provider_ids[old] = new
                attempt["provider_return_id"] = new
                attempt["attempt_id"] = (
                    f"{attempt['attempt_id']}-two-{index:02d}"
                )
            for reaction in response["per_creative_reactions"]:
                reaction["variation_id"] = replacements[
                    reaction["variation_id"]
                ]
                reaction["reaction_id"] += f"-two-{index:02d}"
                provenance = reaction["source_provenance"]
                provenance["provider_return_id"] = provider_ids[
                    provenance["provider_return_id"]
                ]
            choice = response["pairwise_choice"]
            choice["status"] = "first_preferred"
            choice["preferred_variation_id"] = response["shown_order"][0]
            choice["reason"] = "The first shown creative is more persuasive."
            provenance = choice["source_provenance"]
            provenance["provider_return_id"] = provider_ids[
                provenance["provider_return_id"]
            ]
            choice["frozen_reaction_ids"] = [
                item["reaction_id"]
                for item in response["per_creative_reactions"]
            ]
            responses.append(response)

        pair_root = self.base / "two-arm-pairwise"
        pair_root.mkdir()
        creative_hashes = manifest["outputs"]["creative_asset_hashes"]
        pair_manifest = matching_manifest(
            records=responses,
            creative_ids=tuple(sorted(creative_hashes)),
            shortlist_size=2,
        )
        pair_manifest["outputs"]["creative_asset_hashes"] = deepcopy(
            creative_hashes
        )
        pair_manifest["synthetic_replicate_capacity"] = deepcopy(
            manifest["synthetic_replicate_capacity"]
        )
        pair_manifest["maximum_synthetic_panelists"] = manifest[
            "maximum_synthetic_panelists"
        ]
        pair_run, *_ = _bind_without_semantic_validation(
            pair_root, pair_manifest, _accepted_workflow(responses),
        )
        screening_path = pair_root / "screening-model-results.json"
        result_path = maxdiff_world["result_path"]
        assert isinstance(result_path, Path)
        screening_path.write_bytes(result_path.read_bytes())
        pair_result = pair_root / "boundary-results.json"
        result_binding = record["result_binding"]
        assert isinstance(result_binding, dict)
        max_receipt = self.evidence_root / _receipt_name(
            "maxdiff_screening_ordering",
            str(record["run_id"]),
            str(result_binding["canonical_document_sha256"]),
        )
        inputs = ProducerReplayInputs(
            study_manifest=pair_run / "study-manifest.json",
            accepted_responses=pair_run / "panelist-responses.jsonl",
            raw_provider_returns=pair_run / "raw-provider-returns.jsonl",
            rejected_attempts=pair_run / "rejected-attempts.jsonl",
            cumulative_dispatch_audit=pair_run / "dispatch-audit.jsonl",
            result=pair_result,
            screening_jobs=None,
            recovery_configuration=None,
            command_dispatch_audit_input=None,
            screening_result=screening_path,
            screening_producer_evidence=max_receipt,
        )
        self._run([
            sys.executable,
            str(
                ROOT
                / "skills/audience-ad-testing-lab/scripts/aggregate-screening.py"
            ),
            "boundary",
            "--manifest", str(inputs.study_manifest),
            "--screening-results", str(inputs.screening_result),
            "--responses", str(inputs.accepted_responses),
            "--output", str(pair_result),
        ])
        pair_record = verify_synthetic_producer(
            surface="pairwise_boundary_ordering",
            inputs=inputs,
            allowed_source_roots=[ROOT, pair_root, self.evidence_root],
            runtime_root=ROOT,
            snapshot_root=self.snapshot_root,
            evidence_root=self.evidence_root,
        )
        world_key = "pairwise_two_candidate"
        self._remember(
            world_key, pair_record, pair_result,
            pair_run / "study-manifest.json",
        )
        return world_key

    def registration(
        self, surface: str, *, world_key: str | None = None,
    ) -> dict[str, object]:
        world = self.surfaces[world_key or surface]
        record = world["record"]
        manifest = world["manifest"]
        assert isinstance(record, dict) and isinstance(manifest, dict)
        result_binding = record["result_binding"]
        inputs = record["input_bindings"]
        semantics = record["producer_semantics"]
        assert isinstance(result_binding, dict)
        assert isinstance(inputs, dict)
        assert isinstance(semantics, dict)
        hashes = manifest["outputs"]["creative_asset_hashes"]
        registration = preregistration_fixture()
        registration["synthetic_surface"] = {
            "surface": surface,
            "method": record["method"],
            "stage": record["stage"],
            "run_id": record["run_id"],
            "result_path": result_binding["path"],
            "result_sha256": result_binding["canonical_document_sha256"],
            "result_bytes_sha256": result_binding["raw_bytes_sha256"],
            "manifest_sha256": inputs["study_manifest"][
                "canonical_document_sha256"
            ],
            "lineage_bundle_sha256": synthetic.lineage_bundle_sha256({
                role: inputs[role] for role in synthetic.LINEAGE_ORDER
            }),
            "producer_evidence_sha256": record[
                "producer_evidence_sha256"
            ],
            "producer_semantics_sha256": semantics[
                "producer_semantics_sha256"
            ],
            "frozen_at": record["frozen_at"],
            "producer_evidence_sealed_at": record["sealed_at"],
            "eligible_creatives": [{
                "creative_id": creative_id,
                "creative_sha256": creative_sha,
            } for creative_id, creative_sha in sorted(hashes.items())],
        }
        sealed_at = str(record["sealed_at"])
        if str(registration["registered_at"]) < sealed_at:
            registration["registered_at"] = sealed_at
        compact = {
            "surface": surface,
            "run_id": record["run_id"],
            "result_sha256": result_binding["canonical_document_sha256"],
        }
        registration["claim_scope"]["synthetic_binding"] = compact
        registration["analysis_rules"]["tie_handling"] = synthetic._tie_policy(
            surface=surface, evidence=record,
        )
        result = world["result"]
        assert isinstance(result, dict)
        registration["validation_blocks"][0]["planned_arm_ids"] = [
            f"arm-{creative_id.lower()}"
            for creative_id in result["ranked_ids"]
        ]
        registration["registration_sha256"] = None
        return seal_preregistration(registration)

    def observations(
        self, surface: str, registration: dict[str, object],
        *, world_key: str | None = None,
    ) -> list[dict[str, object]]:
        world = self.surfaces[world_key or surface]
        result = world["result"]
        manifest = world["manifest"]
        assert isinstance(result, dict) and isinstance(manifest, dict)
        hashes = manifest["outputs"]["creative_asset_hashes"]
        rows = []
        for index, creative_id in enumerate(result["ranked_ids"]):
            document = observation_fixture()
            binding = document["registration_binding"]
            assert isinstance(binding, dict)
            binding.update({
                "registration_id": registration["registration_id"],
                "registration_sha256": registration["registration_sha256"],
                "registered_at": registration["registered_at"],
                "status": registration["status"],
                "holdout_partition": registration["holdout_partition"],
                "claim_scope": registration["claim_scope"],
                "multiplicity_rules": registration["multiplicity_rules"],
                "preregistration": registration,
            })
            document["synthetic_binding"] = {
                "surface": surface,
                "run_id": registration["synthetic_surface"]["run_id"],
                "result_sha256": registration["synthetic_surface"][
                    "result_sha256"
                ],
            }
            document["panel_binding"] = registration["panel_binding"]
            document["claim_scope"] = registration["claim_scope"]
            document["arm_id"] = f"arm-{creative_id.lower()}"
            document["observation_id"] = f"observation-{creative_id.lower()}"
            document["creative_binding"] = {
                "creative_id": creative_id,
                "creative_sha256": hashes[creative_id],
            }
            document["aggregate"] = {
                "success_count": 80 - index * 10,
                "eligible_exposure_count": 100,
            }
            shared = project_shared_outcome_evidence(document)
            document["shared_outcome_evidence_binding"] = {
                "shared_evidence_id": shared["shared_evidence_id"],
                "study_id": shared["study_id"],
                "shared_evidence_sha256": shared["shared_evidence_sha256"],
            }
            document["observation_sha256"] = sha256_json({
                **document, "observation_sha256": None,
            })
            rows.append(document)
        return rows

    def compare(
        self, surface: str, *, world_key: str | None = None,
    ) -> dict[str, object]:
        registration = self.registration(surface, world_key=world_key)
        result = self.surfaces[world_key or surface]["result"]
        assert isinstance(result, dict)
        return build_synthetic_outcome_comparison(
            registration=registration,
            result=result,
            evidence_root=self.evidence_root,
            snapshot_root=self.snapshot_root,
            observations=self.observations(
                surface, registration, world_key=world_key,
            ),
        )


class Tier4SyntheticComparisonTests(unittest.TestCase):
    def test_real_task7_authority_all_surfaces_and_attack_matrix(self) -> None:
        world = RealProducerWorld(self)
        for surface in SURFACES:
            with self.subTest(surface=surface):
                comparison = world.compare(surface)
                self.assertEqual(comparison, validate_comparison(comparison))
            registration = world.registration(surface)
            tie_policy = registration["analysis_rules"]["tie_handling"]
            assert isinstance(tie_policy, dict)
            for field in tuple(tie_policy):
                changed = deepcopy(registration)
                tie = changed["analysis_rules"]["tie_handling"]
                assert isinstance(tie, dict)
                if field == "effective_ordering_tolerance":
                    tie[field] = float(tie[field]) + 0.001
                else:
                    tie[field] = str(tie[field]) + "-changed"
                changed["registration_sha256"] = None
                try:
                    changed = seal_preregistration(changed)
                except ContractError:
                    # Contract-fixed policy strings fail at sealing. The
                    # numeric authenticated field reaches the Task 7 adapter.
                    continue
                result = world.surfaces[surface]["result"]
                assert isinstance(result, dict)
                with self.subTest(
                    real_tie_surface=surface, real_tie_field=field,
                ), self.assertRaisesRegex(ContractError, "tie handling"):
                    build_synthetic_outcome_comparison(
                        registration=changed,
                        result=result,
                        evidence_root=world.evidence_root,
                        snapshot_root=world.snapshot_root,
                        observations=world.observations(
                            surface, registration,
                        ),
                    )

        boundary_maxdiff = world.surfaces[
            "maxdiff_screening_ordering"
        ]["result"]
        assert isinstance(boundary_maxdiff, dict)
        self.assertEqual(
            "boundary_required", boundary_maxdiff["selection_status"],
        )
        self.assertIn("boundary_plan", boundary_maxdiff)
        self.assertEqual(
            boundary_maxdiff,
            synthetic._result_document(
                boundary_maxdiff, surface="maxdiff_screening_ordering",
            ),
        )

        resolved_world = world.build_maxdiff_variant(
            slug="resolved-maxdiff", best_pattern=("v1", "v2"),
        )
        resolved_maxdiff = resolved_world["result"]
        assert isinstance(resolved_maxdiff, dict)
        self.assertEqual("resolved", resolved_maxdiff["selection_status"])
        self.assertNotIn("boundary_plan", resolved_maxdiff)
        self.assertEqual(
            resolved_maxdiff,
            synthetic._result_document(
                resolved_maxdiff, surface="maxdiff_screening_ordering",
            ),
        )
        resolved_registration = world.registration(
            "maxdiff_screening_ordering", world_key="resolved-maxdiff",
        )
        resolved_ordering = load_frozen_ordering(
            surface="maxdiff_screening_ordering",
            result=resolved_maxdiff,
            registration=resolved_registration,
            evidence_root=world.evidence_root,
            snapshot_root=world.snapshot_root,
        )
        self.assertEqual(
            tuple(resolved_maxdiff["ranked_ids"]),
            tuple(
                creative_id
                for group in resolved_ordering.ordered_groups
                for creative_id in group
            ),
        )
        resolved_comparison = world.compare(
            "maxdiff_screening_ordering",
            world_key="resolved-maxdiff",
        )
        self.assertEqual(
            resolved_comparison, validate_comparison(resolved_comparison),
        )
        closed_shape_attacks = (
            (
                "resolved-with-plan",
                {**resolved_maxdiff, "boundary_plan": {}},
            ),
            (
                "boundary-without-plan",
                {
                    key: value
                    for key, value in boundary_maxdiff.items()
                    if key != "boundary_plan"
                },
            ),
            (
                "unknown-status",
                {**resolved_maxdiff, "selection_status": "unresolved"},
            ),
        )
        for label, changed in closed_shape_attacks:
            with self.subTest(maxdiff_closed_shape=label), self.assertRaises(
                ContractError,
            ):
                synthetic._result_document(
                    changed, surface="maxdiff_screening_ordering",
                )

        two_boundary_world = world.build_maxdiff_variant(
            slug="two-boundary-maxdiff",
            best_pattern=(
                "v1", "v1", "v2", "v3",
                "v1", "v1", "v2", "v3",
            ),
        )
        two_boundary_result = two_boundary_world["result"]
        assert isinstance(two_boundary_result, dict)
        self.assertEqual(
            ["v2", "v3"],
            sorted(
                creative_id
                for creative_id, classification in
                two_boundary_result["classifications"].items()
                if classification == "boundary_candidate"
            ),
        )
        two_pair_key = world.build_two_candidate_pairwise(
            two_boundary_world,
        )
        two_pair_result = world.surfaces[two_pair_key]["result"]
        assert isinstance(two_pair_result, dict)
        self.assertEqual("resolved", two_pair_result["status"])
        self.assertEqual(2, len(two_pair_result["ranked_ids"]))
        two_registration = world.registration(
            "pairwise_boundary_ordering", world_key=two_pair_key,
        )
        two_ordering = load_frozen_ordering(
            surface="pairwise_boundary_ordering",
            result=two_pair_result,
            registration=two_registration,
            evidence_root=world.evidence_root,
            snapshot_root=world.snapshot_root,
        )
        self.assertEqual(
            {"v2", "v3"},
            {
                creative_id
                for group in two_ordering.ordered_groups
                for creative_id in group
            },
        )
        with self.assertRaisesRegex(ContractError, "at least three"):
            build_synthetic_outcome_comparison(
                registration=two_registration,
                result=two_pair_result,
                evidence_root=world.evidence_root,
                snapshot_root=world.snapshot_root,
                observations=world.observations(
                    "pairwise_boundary_ordering",
                    two_registration,
                    world_key=two_pair_key,
                ),
            )

        surface = "complete_exposure_ordering"
        registration = world.registration(surface)
        result = world.surfaces[surface]["result"]
        record = world.surfaces[surface]["record"]
        assert isinstance(result, dict) and isinstance(record, dict)
        observations = world.observations(surface, registration)

        for label, evidence_root, snapshot_root in (
            (
                "evidence-root",
                world.base / "wrong-evidence",
                world.snapshot_root,
            ),
            (
                "snapshot-root",
                world.evidence_root,
                world.base / "wrong-snapshots",
            ),
        ):
            evidence_root.mkdir(exist_ok=True)
            snapshot_root.mkdir(exist_ok=True)
            with self.subTest(substitution=label), self.assertRaises((
                ContractError, ProducerEvidenceError,
            )):
                build_synthetic_outcome_comparison(
                    registration=registration,
                    result=result,
                    evidence_root=evidence_root,
                    snapshot_root=snapshot_root,
                    observations=observations,
                )

        result_sha = record["result_binding"]["canonical_document_sha256"]
        run_id = str(record["run_id"])
        receipt = world.evidence_root / _receipt_name(
            surface, run_id, str(result_sha),
        )
        commit = world.snapshot_root / (
            str(record["snapshot_binding"]["snapshot_id"]) + ".snapshot.json"
        )
        commit_document = json.loads(commit.read_bytes())
        archive = world.snapshot_root / commit_document["archive_name"]

        def corrupt_then_restore(path: Path, *, offset: int = 0) -> None:
            raw = path.read_bytes()
            mode = path.stat().st_mode & 0o777
            path.chmod(0o600)
            changed = bytearray(raw)
            changed[offset] ^= 1
            path.write_bytes(changed)
            path.chmod(mode)
            try:
                with self.assertRaises((
                    ContractError, ProducerEvidenceError,
                )):
                    build_synthetic_outcome_comparison(
                        registration=registration,
                        result=result,
                        evidence_root=world.evidence_root,
                        snapshot_root=world.snapshot_root,
                        observations=observations,
                    )
            finally:
                path.chmod(0o600)
                path.write_bytes(raw)
                path.chmod(mode)

        with zipfile.ZipFile(archive) as snapshot_zip:
            first_member = snapshot_zip.infolist()[0]
        archive_raw = archive.read_bytes()
        header = first_member.header_offset
        name_length = int.from_bytes(
            archive_raw[header + 26:header + 28], "little",
        )
        extra_length = int.from_bytes(
            archive_raw[header + 28:header + 30], "little",
        )
        first_member_offset = header + 30 + name_length + extra_length

        for label, path in (
            ("receipt", receipt),
            ("commit", commit),
            ("archive-header", archive),
        ):
            with self.subTest(substitution=label):
                corrupt_then_restore(path)
        with self.subTest(substitution="archive-member"):
            corrupt_then_restore(archive, offset=first_member_offset)

        alternative = world.surfaces["maxdiff_screening_ordering"]["record"]
        assert isinstance(alternative, dict)
        alternative_binding = alternative["result_binding"]
        alternative_snapshot = alternative["snapshot_binding"]
        assert isinstance(alternative_binding, dict)
        assert isinstance(alternative_snapshot, dict)
        alternative_receipt = world.evidence_root / _receipt_name(
            "maxdiff_screening_ordering",
            str(alternative["run_id"]),
            str(alternative_binding["canonical_document_sha256"]),
        )
        alternative_commit = world.snapshot_root / (
            str(alternative_snapshot["snapshot_id"]) + ".snapshot.json"
        )
        alternative_commit_document = json.loads(
            alternative_commit.read_bytes()
        )
        alternative_archive = world.snapshot_root / (
            alternative_commit_document["archive_name"]
        )

        def substitute_then_restore(target: Path, substitute: Path) -> None:
            raw = target.read_bytes()
            mode = target.stat().st_mode & 0o777
            target.chmod(0o600)
            target.write_bytes(substitute.read_bytes())
            target.chmod(mode)
            try:
                with self.assertRaises((
                    ContractError, ProducerEvidenceError,
                )):
                    build_synthetic_outcome_comparison(
                        registration=registration,
                        result=result,
                        evidence_root=world.evidence_root,
                        snapshot_root=world.snapshot_root,
                        observations=observations,
                    )
            finally:
                target.chmod(0o600)
                target.write_bytes(raw)
                target.chmod(mode)

        for label, target, substitute in (
            ("receipt-cross-surface", receipt, alternative_receipt),
            ("commit-cross-surface", commit, alternative_commit),
            ("archive-cross-surface", archive, alternative_archive),
        ):
            with self.subTest(substitution=label):
                substitute_then_restore(target, substitute)
        recovered = world.compare(surface)
        self.assertEqual(recovered, validate_comparison(recovered))

        marker = _publish_revocation(
            surface=surface,
            run_id=run_id,
            result_sha256=str(result_sha),
            evidence_root=world.evidence_root,
        )
        self.assertEqual("revoked", marker["status"])
        try:
            with self.assertRaises((
                ContractError, ProducerEvidenceError,
            )):
                build_synthetic_outcome_comparison(
                    registration=registration,
                    result=result,
                    evidence_root=world.evidence_root,
                    snapshot_root=world.snapshot_root,
                    observations=observations,
                )
        finally:
            revocation = world.evidence_root / _revocation_name(
                surface, run_id, str(result_sha),
            )
            revocation.chmod(0o600)
            revocation.unlink()

        mutations = (
            ("registration", lambda row: row["registration_binding"].__setitem__("registration_sha256", digest("f"))),
            ("panel", lambda row: row["panel_binding"].__setitem__("panel_sha256", digest("f"))),
            ("package", lambda row: row["panel_binding"].__setitem__("package_sha256", digest("f"))),
            ("run", lambda row: row["synthetic_binding"].__setitem__("run_id", "wrong")),
            ("result", lambda row: row["synthetic_binding"].__setitem__("result_sha256", digest("f"))),
            ("metric", lambda row: row["metric"].__setitem__("name", "wrong")),
            ("family", lambda row: row.__setitem__("metric_family", "continuous_mean")),
            ("unit", lambda row: row["units"].__setitem__("outcome", "wrong")),
            ("window", lambda row: row["windows"].__setitem__("measurement", "wrong")),
            ("assignment", lambda row: row["assignment"].__setitem__("unit", "wrong")),
            ("cohort", lambda row: row["outcome_scope"].__setitem__("cohort_id", "wrong")),
            ("segment", lambda row: row["outcome_scope"].__setitem__("segment_id", "wrong")),
            ("channel", lambda row: row["outcome_scope"].__setitem__("channel", "wrong")),
            ("placement", lambda row: row["outcome_scope"].__setitem__("placement", "wrong")),
            ("objective", lambda row: row["outcome_scope"].__setitem__("objective", "wrong")),
            ("geography", lambda row: row["outcome_scope"].__setitem__("geography", "wrong")),
            ("validation-window", lambda row: row["outcome_scope"].__setitem__("validation_window", "wrong")),
            ("study", lambda row: row["shared_outcome_evidence_binding"].__setitem__("study_id", "wrong")),
            ("block", lambda row: row.__setitem__("block_id", "wrong")),
            ("arm", lambda row: row.__setitem__("arm_id", "wrong")),
            ("creative", lambda row: row["creative_binding"].__setitem__("creative_sha256", digest("f"))),
        )
        for label, mutate in mutations:
            with self.subTest(observation_binding=label):
                changed = deepcopy(observations)
                mutate(changed[0])
                changed[0]["observation_sha256"] = sha256_json({
                    **changed[0], "observation_sha256": None,
                })
                with self.assertRaises(ContractError):
                    build_synthetic_outcome_comparison(
                        registration=registration,
                        result=result,
                        evidence_root=world.evidence_root,
                        snapshot_root=world.snapshot_root,
                        observations=changed,
                    )

    def test_caller_result_identity_is_json_type_sensitive_on_every_surface(self) -> None:
        cases = (
            ("int-float", "requested_top_k", 1.0),
            ("bool-int", "model_diagnostics", {"converged": 1}),
            ("null-string", "model_diagnostics", {"reason": "null"}),
        )
        for surface in SURFACES:
            for name, field, replacement in cases:
                with self.subTest(surface=surface, substitution=name):
                    base = result_fixture(surface)
                    if name == "bool-int":
                        base[field] = {"converged": True}
                    elif name == "null-string":
                        base[field] = {"reason": None}
                    elif field not in base:
                        # Pairwise has no requested_top_k. Use one of its
                        # actual numeric CLI fields instead.
                        base["model_diagnostics"] = {"iterations": 1}
                        field, replacement = "model_diagnostics", {
                            "iterations": 1.0,
                        }
                    fixture = AuthenticatedFixture(
                        self, surface=surface, result=base,
                    )
                    supplied = deepcopy(base)
                    supplied[field] = replacement
                    with patch.object(
                        synthetic,
                        "validate_synthetic_producer_evidence",
                        return_value=fixture.receipt,
                    ), patch.object(
                        synthetic, "open_evidence_snapshot",
                        fixture.open_snapshot,
                    ), self.assertRaisesRegex(ContractError, "caller result"):
                        load_frozen_ordering(
                            surface=surface,
                            result=supplied,
                            registration=fixture.registration,
                            evidence_root=fixture.evidence_root,
                            snapshot_root=fixture.snapshot_root,
                        )

    def test_canonical_json_identity_is_cycle_safe_and_resource_bounded(
        self,
    ) -> None:
        scripts = (
            "value=[]; value.append(value)",
            "left=[]; right=[left]; left.append(right); value=left",
        )
        for source in scripts:
            program = (
                "from audience_panel_builder.common import ContractError\n"
                "from audience_panel_builder.population.validation.synthetic "
                "import _canonical_identity\n"
                f"{source}\n"
                "try:\n"
                "    _canonical_identity(value, path='probe')\n"
                "except ContractError:\n"
                "    pass\n"
                "else:\n"
                "    raise SystemExit(2)\n"
            )
            completed = subprocess.run(
                [sys.executable, "-c", program],
                env={
                    "LANG": "C",
                    "LC_ALL": "C",
                    "PYTHONPATH": str(
                        ROOT / "skills/audience-panel-builder/scripts"
                    ),
                    "PYTHONDONTWRITEBYTECODE": "1",
                },
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=2,
                check=False,
            )
            self.assertEqual(
                0, completed.returncode,
                (completed.stdout, completed.stderr),
            )

        shared = {"value": [1]}
        self.assertEqual(
            _canonical_identity(
                [shared, shared], path="shared non-cyclic value",
            ),
            _canonical_identity(
                [{"value": [1]}, {"value": [1]}],
                path="duplicated non-cyclic value",
            ),
        )

        depth_limits = _JsonLimits(
            maximum_depth=3,
            maximum_nodes=20,
            maximum_container_items=20,
            maximum_object_keys=4,
            maximum_string_bytes=8,
        )
        at_depth = [[[]]]
        counters = _JsonCounters()
        self.assertIs(
            counters,
            _finite_json(
                at_depth, "depth", limits=depth_limits,
                counters=counters,
            ),
        )
        self.assertEqual(3, counters.maximum_depth_seen)
        with self.assertRaisesRegex(ContractError, "depth"):
            _finite_json(
                [[[[]]]], "depth", limits=depth_limits,
            )

        def limits(**changes: int) -> _JsonLimits:
            values = {
                "maximum_depth": 8,
                "maximum_nodes": 20,
                "maximum_container_items": 20,
                "maximum_object_keys": 4,
                "maximum_string_bytes": 8,
            }
            values.update(changes)
            return _JsonLimits(**values)

        equality_and_one_over = (
            (
                "nodes", [1, 2],
                limits(maximum_nodes=3),
                limits(maximum_nodes=2),
            ),
            (
                "container-item", [1, 2],
                limits(maximum_container_items=2),
                limits(maximum_container_items=1),
            ),
            (
                "object-key", {"a": 1, "b": 2},
                limits(maximum_object_keys=2),
                limits(maximum_object_keys=1),
            ),
            (
                "string byte", "€",
                limits(maximum_string_bytes=3),
                limits(maximum_string_bytes=2),
            ),
            (
                "key byte", {"€": 1},
                limits(maximum_string_bytes=3),
                limits(maximum_string_bytes=2),
            ),
        )
        for label, value, accepted, rejected in equality_and_one_over:
            with self.subTest(resource=label):
                _finite_json(value, label, limits=accepted)
                with self.assertRaises(ContractError):
                    _finite_json(value, label, limits=rejected)

        with self.assertRaisesRegex(ContractError, "bounded JSON"):
            _finite_json("\ud800", "surrogate")
        for error in (
            TypeError, ValueError, OverflowError, RecursionError,
            MemoryError, UnicodeError,
        ):
            with self.subTest(canonical_failure=error.__name__), patch.object(
                synthetic, "canonical_json_bytes", side_effect=error(),
            ), self.assertRaisesRegex(ContractError, "canonical JSON"):
                _canonical_identity({"safe": True}, path="serializer")

    def test_observation_metric_binding_is_canonical_and_closed(self) -> None:
        fixture = AuthenticatedFixture(self)
        changed_registration = deepcopy(fixture.registration)
        changed_registration["primary_metric"][  # type: ignore[index]
            "practical_equivalence_margin"
        ] = 1
        changed_registration["registration_sha256"] = None
        fixture.registration = seal_preregistration(changed_registration)
        observations = fixture.observations()
        for observation in observations:
            observation["metric"][  # type: ignore[index]
                "practical_equivalence_margin"
            ] = 1.0
            reseal_observation(observation)
        with self.assertRaisesRegex(ContractError, "metric"):
            fixture.compare(observations)

        fixture = AuthenticatedFixture(self)
        metric_attacks = (
            (
                "bool-int",
                lambda metric: metric.__setitem__(
                    "practical_equivalence_margin", True,
                ),
            ),
            (
                "null-string",
                lambda metric: metric.__setitem__("name", None),
            ),
            (
                "string-null",
                lambda metric: metric.__setitem__(
                    "practical_equivalence_margin", "0.02",
                ),
            ),
            (
                "missing",
                lambda metric: metric.pop("definition"),
            ),
            (
                "extra",
                lambda metric: metric.__setitem__("unregistered", "value"),
            ),
        )
        for label, mutate in metric_attacks:
            with self.subTest(metric_attack=label):
                observations = fixture.observations()
                metric = observations[0]["metric"]
                assert isinstance(metric, dict)
                mutate(metric)
                reseal_observation(observations[0])
                with self.assertRaises(ContractError):
                    fixture.compare(observations)

    def test_outcomes_cannot_change_frozen_bucket_groups(self) -> None:
        for surface in (
            "maxdiff_screening_ordering", "pairwise_boundary_ordering",
        ):
            fixture = AuthenticatedFixture(
                self,
                surface=surface,
                tolerance=0.5,
                utilities={
                    "creative-a": 2.75,
                    "creative-b": 2.25,
                    "creative-c": 2.0,
                },
            )
            low_a = fixture.compare(
                fixture.observations((1, 50, 99)),
            )
            high_a = fixture.compare(
                fixture.observations((99, 50, 1)),
            )
            with self.subTest(surface=surface):
                self.assertEqual(
                    low_a["synthetic_ordering"],
                    high_a["synthetic_ordering"],
                )
                self.assertEqual(
                    [["creative-a"], ["creative-b", "creative-c"]],
                    low_a["synthetic_ordering"],
                )

    def test_tie_policy_identity_is_canonical_and_type_sensitive(self) -> None:
        for surface in (
            "maxdiff_screening_ordering", "pairwise_boundary_ordering",
        ):
            fixture = AuthenticatedFixture(
                self, surface=surface, tolerance=1.0,
            )
            changed = deepcopy(fixture.registration)
            changed["analysis_rules"]["tie_handling"][  # type: ignore[index]
                "effective_ordering_tolerance"
            ] = 1
            changed["registration_sha256"] = None
            changed = seal_preregistration(changed)
            with patch.object(
                synthetic,
                "validate_synthetic_producer_evidence",
                return_value=fixture.receipt,
            ), patch.object(
                synthetic, "open_evidence_snapshot",
                fixture.open_snapshot,
            ), self.assertRaisesRegex(ContractError, "tie handling"):
                load_frozen_ordering(
                    surface=surface,
                    result=fixture.result,
                    registration=changed,
                    evidence_root=fixture.evidence_root,
                    snapshot_root=fixture.snapshot_root,
                )

    def test_bucket_arithmetic_accepts_every_finite_producer_quotient(self) -> None:
        policy = {"effective_ordering_tolerance": 1.0}
        above_exact_integer_range = float(2**54)
        self.assertEqual(
            (("a",), ("b",)),
            _ordered_groups(
                surface="maxdiff_screening_ordering",
                ranked_ids=("a", "b"),
                utilities={
                    "a": above_exact_integer_range,
                    "b": above_exact_integer_range - 4.0,
                },
                policy=policy,
            ),
        )
        with self.assertRaisesRegex(ContractError, "quotient"):
            _ordered_groups(
                surface="maxdiff_screening_ordering",
                ranked_ids=("a", "b"),
                utilities={"a": sys.float_info.max, "b": 0.0},
                policy={
                    "effective_ordering_tolerance": sys.float_info.min,
                },
            )
        self.assertEqual(
            (("a",), ("b", "c")),
            _ordered_groups(
                surface="maxdiff_screening_ordering",
                ranked_ids=("a", "b", "c"),
                utilities={"a": 2.75, "b": 2.25, "c": 2.0},
                policy={"effective_ordering_tolerance": 0.5},
            ),
        )

    def test_loads_a_real_durable_task7_receipt_and_snapshot(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        base = Path(temporary.name)
        sources = base / "sources"
        evidence_root = base / "evidence"
        snapshot_root = base / "snapshots"
        sources.mkdir()
        evidence_root.mkdir()
        snapshot_root.mkdir()

        result = result_fixture(
            "complete_exposure_ordering",
            utilities={
                "creative-a": 3.0,
                "creative-b": 2.0,
                "creative-c": 1.0,
            },
        )
        result["study_id"] = "run-001"
        creative_hashes = {
            "creative-a": digest("1"),
            "creative-b": digest("2"),
            "creative-c": digest("3"),
        }
        manifest = {
            "study_id": "run-001",
            "outputs": {"creative_asset_hashes": creative_hashes},
        }

        def write(relative: str, raw: bytes) -> Path:
            path = sources / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(raw)
            return path

        documents = {
            "study_manifest": (
                "inputs/study-manifest.json",
                canonical_json_bytes(manifest), None, "study-manifest.json",
            ),
            "accepted_responses": (
                "inputs/panelist-responses.jsonl",
                canonical_json_bytes({"record": "accepted"}), 1,
                "panelist-responses.jsonl",
            ),
            "raw_provider_returns": (
                "inputs/raw-provider-returns.jsonl",
                canonical_json_bytes({"record": "raw"}), 1,
                "raw-provider-returns.jsonl",
            ),
            "rejected_attempts": (
                "inputs/rejected-attempts.jsonl",
                canonical_json_bytes({"record": "rejected"}), 1,
                "rejected-attempts.jsonl",
            ),
            "dispatch_audit": (
                "inputs/dispatch-audit.jsonl",
                canonical_json_bytes({"record": "dispatch"}), 1,
                "dispatch-audit.jsonl",
            ),
            "screening_jobs": (
                "inputs/screening-jobs.json",
                canonical_json_bytes({"study_id": "run-001"}), None,
                "screening-jobs.json",
            ),
            "screening_response_projection": (
                "inputs/screening-response-projection.jsonl",
                canonical_json_bytes({"record": "projection"}), 1,
                "screening-response-projection.jsonl",
            ),
            "recovery_configuration": (
                "inputs/recovery-configuration.json",
                canonical_json_bytes({"version": "fixture"}), None,
                "recovery-configuration.json",
            ),
            "result": (
                "results/screening-model-results.json",
                json.dumps(result, indent=2, sort_keys=True).encode() + b"\n",
                None, "screening-model-results.json",
            ),
        }
        snapshot_sources: dict[str, Path] = {}
        snapshot_bindings: dict[str, dict[str, object]] = {}
        record_bindings: dict[str, dict[str, object]] = {}
        for role, (member, raw, count, producer_path) in documents.items():
            path = write(member, raw)
            snapshot_sources[member] = path
            parsed = (
                [json.loads(line) for line in raw.decode().splitlines()]
                if count is not None
                else json.loads(raw)
            )
            canonical = (
                b"".join(canonical_json_bytes(item) for item in parsed)
                if count is not None
                else canonical_json_bytes(parsed)
            )
            bound = binding(
                producer_path,
                raw_digest(canonical),
                raw=raw_digest(raw),
                count=count,
            )
            record_bindings[role] = bound
            snapshot_bindings[role] = {
                "member_path": member,
                "raw_bytes_sha256": bound["raw_bytes_sha256"],
                "canonical_document_sha256": bound[
                    "canonical_document_sha256"
                ],
                "record_count": count,
            }
        runtime_member = (
            "runtime/skills/audience-ad-testing-lab/scripts/"
            "aggregate-screening.py"
        )
        runtime_raw = b"# durable Task 7 fixture\n"
        snapshot_sources[runtime_member] = write(runtime_member, runtime_raw)
        result_sha = record_bindings["result"]["canonical_document_sha256"]
        snapshot = create_evidence_snapshot(
            surface="complete_exposure_ordering",
            run_id="run-001",
            result_sha256=result_sha,
            sources=snapshot_sources,
            bindings=snapshot_bindings,
            allowed_roots=[sources],
            snapshot_root=snapshot_root,
        )
        record = deepcopy(valid_record())
        record["run_id"] = "run-001"
        record["frozen_at"] = snapshot.frozen_at
        record["sealed_at"] = snapshot.frozen_at
        record["input_bindings"] = {
            role: record_bindings[role]
            for role in (
                "study_manifest", "accepted_responses",
                "raw_provider_returns", "rejected_attempts",
                "dispatch_audit",
            )
        }
        record["input_bindings"].update({
            "command_dispatch_audit_input": None,
            "screening_jobs": record_bindings["screening_jobs"],
            "screening_response_projection": record_bindings[
                "screening_response_projection"
            ],
            "recovery_configuration": record_bindings[
                "recovery_configuration"
            ],
        })
        record["result_binding"] = record_bindings["result"]
        record["snapshot_binding"] = {
            "snapshot_id": snapshot.snapshot_id,
            "snapshot_sha256": snapshot.snapshot_sha256,
            "archive_sha256": snapshot.archive_sha256,
        }
        semantics = record["producer_semantics"]
        semantics["dependency_closure"] = [{
            "path": (
                "skills/audience-ad-testing-lab/scripts/"
                "aggregate-screening.py"
            ),
            "byte_count": len(runtime_raw),
            "raw_bytes_sha256": raw_digest(runtime_raw),
        }]
        semantics["policy_bindings"]["recovery_configuration_sha256"] = (
            record_bindings["recovery_configuration"][
                "canonical_document_sha256"
            ]
        )
        semantics["producer_semantics_sha256"] = None
        semantics["producer_semantics_sha256"] = sha256_json(semantics)
        record["producer_evidence_sha256"] = None
        record["producer_evidence_sha256"] = sha256_json(record)
        with patch.object(producer_evidence, "_validate_snapshot"):
            _publish_receipt(
                record,
                evidence_root=evidence_root,
                snapshot_root=snapshot_root,
            )

        registration = preregistration_fixture()
        registration["synthetic_surface"] = {
            "surface": "complete_exposure_ordering",
            "method": "complete_exposure",
            "stage": "screening",
            "run_id": "run-001",
            "result_path": "screening-model-results.json",
            "result_sha256": result_sha,
            "result_bytes_sha256": record_bindings["result"][
                "raw_bytes_sha256"
            ],
            "manifest_sha256": record_bindings["study_manifest"][
                "canonical_document_sha256"
            ],
            "lineage_bundle_sha256": synthetic.lineage_bundle_sha256({
                role: record_bindings[role]
                for role in synthetic.LINEAGE_ORDER
            }),
            "producer_evidence_sha256": record[
                "producer_evidence_sha256"
            ],
            "producer_semantics_sha256": semantics[
                "producer_semantics_sha256"
            ],
            "frozen_at": snapshot.frozen_at,
            "producer_evidence_sealed_at": snapshot.frozen_at,
            "eligible_creatives": [
                {
                    "creative_id": creative_id,
                    "creative_sha256": creative_sha,
                }
                for creative_id, creative_sha in sorted(
                    creative_hashes.items()
                )
            ],
        }
        if str(registration["registered_at"]) < str(snapshot.frozen_at):
            registration["registered_at"] = snapshot.frozen_at
        compact = {
            "surface": "complete_exposure_ordering",
            "run_id": "run-001",
            "result_sha256": result_sha,
        }
        registration["claim_scope"]["synthetic_binding"] = compact
        registration["validation_blocks"][0]["planned_arm_ids"] = [
            "arm-a", "arm-b", "arm-c",
        ]
        registration["registration_sha256"] = None
        sealed = seal_preregistration(registration)
        # Receipt lookup, deterministic naming, canonical receipt parsing,
        # durability recovery, revocation checks, and the independently opened
        # snapshot are real.  The Task 7 scientific-runtime replay validation
        # is already covered by its own protected suite and is not repeated by
        # this focused comparison fixture.
        with patch.object(producer_evidence, "_validate_snapshot"):
            ordering = load_frozen_ordering(
                surface="complete_exposure_ordering",
                result=result,
                registration=sealed,
                evidence_root=evidence_root,
                snapshot_root=snapshot_root,
            )
        self.assertEqual(
            (("creative-a",), ("creative-b",), ("creative-c",)),
            ordering.ordered_groups,
        )

    def test_public_api_denies_bare_ordering_at_claim_boundary(self) -> None:
        self.assertEqual(
            {
                "surface", "result", "registration",
                "evidence_root", "snapshot_root",
            },
            set(inspect.signature(load_frozen_ordering).parameters),
        )
        self.assertEqual(
            {"ordering"},
            set(inspect.signature(derive_pair_directions).parameters),
        )
        self.assertEqual(
            {
                "registration", "result", "evidence_root",
                "snapshot_root", "observations",
            },
            set(inspect.signature(build_synthetic_outcome_comparison).parameters),
        )
        with self.assertRaises(TypeError):
            build_synthetic_outcome_comparison(  # type: ignore[call-arg]
                registration={}, ordering=FrozenOrdering(
                    "complete_exposure_ordering", "run", digest("1"),
                    (("a",), ("b",), ("c",)), (),
                ), observations=[],
            )

    def test_actual_cli_shapes_round_trip_all_three_surfaces(self) -> None:
        for surface in SURFACES:
            with self.subTest(surface=surface):
                fixture = AuthenticatedFixture(self, surface=surface)
                ordering = fixture.load()
                self.assertEqual(surface, ordering.surface)
                self.assertEqual(
                    tuple(fixture.result["ranked_ids"]),
                    tuple(item for group in ordering.ordered_groups for item in group),
                )

    def test_result_allowlists_and_audience_authority_are_atomic(self) -> None:
        for surface in SURFACES:
            with self.subTest(surface=surface):
                base = result_fixture(surface)
                for keys, succeeds in (
                    ((), True),
                    (("audience_package",), False),
                    (("audience_lock",), False),
                    (("audience_package", "audience_lock"), True),
                    (("extra",), False),
                ):
                    changed = deepcopy(base)
                    for key in keys:
                        changed[key] = {}
                    fixture = AuthenticatedFixture(
                        self, surface=surface, result=changed,
                    )
                    if succeeds:
                        fixture.load()
                    else:
                        with self.assertRaisesRegex(
                            ContractError, "fields|appear together",
                        ):
                            fixture.load()

    def test_complete_uses_exact_utility_equality_not_cutoff_tolerance(self) -> None:
        fixture = AuthenticatedFixture(
            self,
            utilities={
                "creative-a": 2.0,
                "creative-b": 1.0 + 1e-13,
                "creative-c": 1.0,
            },
        )
        self.assertEqual(
            (("creative-a",), ("creative-b",), ("creative-c",)),
            fixture.load().ordered_groups,
        )
        tied = AuthenticatedFixture(
            self,
            utilities={
                "creative-a": 2.0,
                "creative-b": 1.0,
                "creative-c": 1.0,
            },
        )
        self.assertEqual(
            (("creative-a",), ("creative-b", "creative-c")),
            tied.load().ordered_groups,
        )

    def test_bucket_surfaces_use_python_half_even_without_reordering(self) -> None:
        for surface in (
            "maxdiff_screening_ordering", "pairwise_boundary_ordering",
        ):
            with self.subTest(surface=surface):
                fixture = AuthenticatedFixture(
                    self,
                    surface=surface,
                    tolerance=0.5,
                    utilities={
                        "creative-a": 2.75,
                        "creative-b": 2.25,
                        "creative-c": 2.0,
                    },
                )
                self.assertEqual(
                    (("creative-a",), ("creative-b", "creative-c")),
                    fixture.load().ordered_groups,
                )
                inconsistent = AuthenticatedFixture(
                    self,
                    surface=surface,
                    tolerance=0.5,
                    utilities={
                        "creative-a": 1.0,
                        "creative-b": 2.0,
                        "creative-c": 0.0,
                    },
                )
                with self.assertRaisesRegex(ContractError, "comparator"):
                    inconsistent.load()

    def test_tie_policy_projection_is_exact_for_every_preregistered_field(self) -> None:
        for surface in SURFACES:
            fixture = AuthenticatedFixture(self, surface=surface)
            policy = fixture.registration["analysis_rules"]["tie_handling"]
            assert isinstance(policy, dict)
            for field in tuple(policy):
                with self.subTest(surface=surface, field=field):
                    changed = deepcopy(fixture.registration)
                    tie = changed["analysis_rules"]["tie_handling"]
                    if field == "effective_ordering_tolerance":
                        tie[field] = 0.002
                    else:
                        tie[field] = str(tie[field]) + "-changed"
                    changed["registration_sha256"] = None
                    # Contract-fixed string mutations fail before evidence;
                    # a valid altered numeric tolerance reaches Task 8 equality.
                    try:
                        changed = seal_preregistration(changed)
                    except ContractError:
                        continue
                    with patch.object(
                        synthetic,
                        "validate_synthetic_producer_evidence",
                        return_value=fixture.receipt,
                    ), patch.object(
                        synthetic, "open_evidence_snapshot",
                        fixture.open_snapshot,
                    ), self.assertRaisesRegex(ContractError, "tie handling"):
                        load_frozen_ordering(
                            surface=surface,
                            result=fixture.result,
                            registration=changed,
                            evidence_root=fixture.evidence_root,
                            snapshot_root=fixture.snapshot_root,
                        )

    def test_pairwise_covers_exact_boundary_subset_while_roster_stays_full(self) -> None:
        fixture = AuthenticatedFixture(
            self,
            surface="pairwise_boundary_ordering",
            ranked=("creative-a", "creative-b", "creative-c"),
            full_roster=(
                "creative-a", "creative-b", "creative-c", "creative-d",
            ),
        )
        ordering = fixture.load()
        self.assertEqual(
            {"creative-a", "creative-b", "creative-c"},
            {item for group in ordering.ordered_groups for item in group},
        )
        self.assertEqual(4, len(
            fixture.registration["synthetic_surface"]["eligible_creatives"]
        ))
        self.assertEqual(
            fixture.compare(),
            validate_comparison(fixture.compare()),
        )

    def test_authentic_two_candidate_receipt_is_c1_ineligible(self) -> None:
        fixture = AuthenticatedFixture(
            self,
            surface="pairwise_boundary_ordering",
            ranked=("creative-a", "creative-b"),
            full_roster=("creative-a", "creative-b", "creative-c"),
        )
        self.assertEqual(2, len(fixture.load().creative_hashes))
        with self.assertRaisesRegex(ContractError, "at least three"):
            fixture.compare()

    def test_every_external_receipt_projection_binding_is_required(self) -> None:
        fixture = AuthenticatedFixture(self)
        mutations = (
            ("method", lambda row: row.__setitem__("method", "wrong")),
            ("stage", lambda row: row.__setitem__("stage", "wrong")),
            ("run", lambda row: row.__setitem__("run_id", "wrong")),
            ("frozen", lambda row: row.__setitem__("frozen_at", "2026-07-29T00:00:00Z")),
            ("sealed", lambda row: row.__setitem__("sealed_at", "2026-07-30T00:00:00Z")),
            ("receipt", lambda row: row.__setitem__("producer_evidence_sha256", digest("f"))),
            ("semantics", lambda row: row["producer_semantics"].__setitem__("producer_semantics_sha256", digest("f"))),
            ("manifest", lambda row: row["input_bindings"]["study_manifest"].__setitem__("canonical_document_sha256", digest("f"))),
            ("result", lambda row: row["result_binding"].__setitem__("canonical_document_sha256", digest("f"))),
            ("raw-result", lambda row: row["result_binding"].__setitem__("raw_bytes_sha256", digest("f"))),
            ("lineage", lambda row: row["input_bindings"]["accepted_responses"].__setitem__("canonical_document_sha256", digest("f"))),
        )
        for name, mutate in mutations:
            with self.subTest(name=name):
                changed = deepcopy(fixture.receipt)
                mutate(changed)
                with patch.object(
                    synthetic,
                    "validate_synthetic_producer_evidence",
                    return_value=changed,
                ), patch.object(
                    synthetic, "open_evidence_snapshot",
                    fixture.open_snapshot,
                ), self.assertRaises(ContractError):
                    load_frozen_ordering(
                        surface=fixture.surface,
                        result=fixture.result,
                        registration=fixture.registration,
                        evidence_root=fixture.evidence_root,
                        snapshot_root=fixture.snapshot_root,
                    )

    def test_snapshot_identity_and_exact_result_copy_are_required(self) -> None:
        fixture = AuthenticatedFixture(self)
        for field in (
            "snapshot_id", "snapshot_sha256", "archive_sha256",
        ):
            with self.subTest(field=field):
                changed = deepcopy(fixture.receipt)
                changed["snapshot_binding"][field] = digest("f")
                with patch.object(
                    synthetic,
                    "validate_synthetic_producer_evidence",
                    return_value=changed,
                ), patch.object(
                    synthetic, "open_evidence_snapshot",
                    fixture.open_snapshot,
                ), self.assertRaisesRegex(ContractError, "snapshot"):
                    load_frozen_ordering(
                        surface=fixture.surface,
                        result=fixture.result,
                        registration=fixture.registration,
                        evidence_root=fixture.evidence_root,
                        snapshot_root=fixture.snapshot_root,
                    )
        supplied = deepcopy(fixture.result)
        supplied["utilities"]["creative-a"] = 99.0
        with patch.object(
            synthetic,
            "validate_synthetic_producer_evidence",
            return_value=fixture.receipt,
        ), patch.object(
            synthetic, "open_evidence_snapshot", fixture.open_snapshot,
        ), self.assertRaisesRegex(ContractError, "caller result"):
            load_frozen_ordering(
                surface=fixture.surface,
                result=supplied,
                registration=fixture.registration,
                evidence_root=fixture.evidence_root,
                snapshot_root=fixture.snapshot_root,
            )

    def test_observations_bind_every_registered_identity(self) -> None:
        fixture = AuthenticatedFixture(self)
        fields = (
            ("panel", lambda row: row["panel_binding"].__setitem__("panel_sha256", digest("f"))),
            ("run", lambda row: row["synthetic_binding"].__setitem__("run_id", "wrong")),
            ("metric", lambda row: row["metric"].__setitem__("name", "wrong")),
            ("unit", lambda row: row["units"].__setitem__("outcome", "wrong")),
            ("window", lambda row: row["windows"].__setitem__("measurement", "wrong")),
            ("assignment", lambda row: row["assignment"].__setitem__("unit", "wrong")),
            ("cohort", lambda row: row["outcome_scope"].__setitem__("cohort_id", "wrong")),
            ("segment", lambda row: row["outcome_scope"].__setitem__("segment_id", "wrong")),
            ("channel", lambda row: row["outcome_scope"].__setitem__("channel", "wrong")),
            ("placement", lambda row: row["outcome_scope"].__setitem__("placement", "wrong")),
            ("objective", lambda row: row["outcome_scope"].__setitem__("objective", "wrong")),
            ("geography", lambda row: row["outcome_scope"].__setitem__("geography", "wrong")),
            ("validation-window", lambda row: row["outcome_scope"].__setitem__("validation_window", "wrong")),
            ("block", lambda row: row.__setitem__("block_id", "wrong")),
            ("arm", lambda row: row.__setitem__("arm_id", "wrong")),
            ("creative", lambda row: row["creative_binding"].__setitem__("creative_sha256", digest("f"))),
        )
        for name, mutate in fields:
            with self.subTest(name=name):
                observations = fixture.observations()
                mutate(observations[0])
                observations[0]["observation_sha256"] = sha256_json({
                    **observations[0], "observation_sha256": None,
                })
                with self.assertRaises(ContractError):
                    fixture.compare(observations)

    def test_synthetic_tie_and_strict_observation_is_not_a_reversal(self) -> None:
        fixture = AuthenticatedFixture(
            self,
            utilities={
                "creative-a": 2.0,
                "creative-b": 1.0,
                "creative-c": 1.0,
            },
        )
        comparison = fixture.compare(successes := fixture.observations((80, 95, 5)))
        del successes
        row = next(
            item for item in comparison["pairwise_comparisons"]
            if {item["creative_a"], item["creative_b"]}
            == {"creative-b", "creative-c"}
        )
        self.assertEqual("synthetic_tie", row["synthetic_direction"])
        self.assertEqual("observed_a_above_b", row["observed_direction"])
        self.assertNotIn("reversal", row)

    def test_mechanical_pair_projection_has_no_claim_fields(self) -> None:
        ordering = FrozenOrdering(
            "complete_exposure_ordering", "run-1", digest("1"),
            (("creative-a",), ("creative-b", "creative-c")),
            (
                ("creative-a", digest("a")),
                ("creative-b", digest("b")),
                ("creative-c", digest("c")),
            ),
        )
        rows = derive_pair_directions(ordering)
        self.assertEqual(3, len(rows))
        self.assertEqual(
            {"creative_a", "creative_b", "synthetic_direction"},
            set(rows[0]),
        )


if __name__ == "__main__":
    unittest.main()
