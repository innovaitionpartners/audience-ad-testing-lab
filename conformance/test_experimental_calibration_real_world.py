"""Release C2 real-world persona-calibration contracts and gates."""

from __future__ import annotations

from copy import deepcopy
import hashlib
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
PANEL_SCRIPTS = ROOT / "skills" / "audience-panel-builder" / "scripts"
AD_SCRIPTS = ROOT / "skills" / "audience-ad-testing-lab" / "scripts"
sys.path[:0] = [str(PANEL_SCRIPTS), str(AD_SCRIPTS)]

from audience_lab.audience_library import (  # noqa: E402
    _requires_experimental_c2_gate,
    find_package,
    list_panels,
    register_package,
)
from audience_lab.audience_package import (  # noqa: E402
    PackageValidationError,
    build_audience_package,
)
from audience_panel_builder.common import ContractError, canonical_json_bytes, sha256_json  # noqa: E402
from audience_panel_builder.population.experimental_calibration.candidate import _panel_binding  # noqa: E402
from audience_panel_builder.population.experimental_calibration.real_world import (  # noqa: E402
    ALTERNATIVE_CAUSES_VERSION,
    CALIBRATION_HISTORY_ACTION,
    build_real_world_persona_behavior_proposal,
    build_registration_proposal,
    diagnose_real_world_persona_behavior,
    materialize_real_world_candidate,
    publish_real_world_candidate_bundle,
    register_real_world_calibrated_package,
    require_registration_approval,
)
from audience_panel_builder.population.validation.package import (  # noqa: E402
    read_authenticated_panel_snapshot,
)
from conformance.experimental_calibration_fixtures import (  # noqa: E402
    candidate_base_panel_fixture,
    creative_attribute_inputs,
    registry_fixture,
)


def digest(character: str, *, prefixed: bool = True) -> str:
    value = hashlib.sha256(character.encode("utf-8")).hexdigest()
    return f"sha256:{value}" if prefixed else value


def alternative_causes(*, uncleared: str | None = None) -> dict[str, object]:
    causes = [
        "attribution", "delivery", "landing-page", "offer", "targeting",
        "timing", "tracking",
    ]
    return {
        "schema_version": ALTERNATIVE_CAUSES_VERSION,
        "reviewed_at": "2026-08-20T00:00:00Z",
        "reviewed_by": "human-reviewer",
        "causes": [
            {
                "cause": cause,
                "status": "not_cleared" if cause == uncleared else "cleared",
                "evidence": f"Reviewed {cause} evidence for this study.",
            }
            for cause in causes
        ],
    }


def base_binding(panel: dict[str, object]) -> dict[str, object]:
    return {
        **_panel_binding(panel, persona_id="finance-pricing-archetype"),
        "package_sha256": digest("base-package"),
    }


def negative_package(
    panel_binding: dict[str, object], *, sequence: int,
    contrary: bool = False,
) -> dict[str, object]:
    source = digest(f"source-{sequence}")
    study_id = f"study-{sequence:02d}"
    pair = {
        "creative_a": "ease-of-use",
        "creative_b": "quantified-payback",
        "synthetic_direction": (
            "synthetic_b_above_a" if contrary else "synthetic_a_above_b"
        ),
        "observed_direction": "observed_b_above_a",
    }
    comparison = {
        "comparison_sha256": digest(f"comparison-{sequence}"),
        "block_binding": {
            "block_id": f"block-{sequence:02d}",
            "study_id": study_id,
        },
        "observations": [{
            "source": {"source_sha256": source},
            "outcome_accessed_at": f"2026-08-{10 + sequence:02d}T00:00:00Z",
        }],
        "segment_evidence": [{
            "segment_id": "finance",
            "pairwise_comparisons": [pair],
        }],
    }
    evaluation = {
        "evaluation_id": f"evaluation-{sequence:02d}",
        "evaluation_sha256": digest(f"evaluation-{sequence}"),
        "evaluated_at": f"2026-08-{15 + sequence:02d}T00:00:00Z",
        "decision": {"status": "tier4_not_supported"},
        "coverage": {"status": "complete"},
        "missingness": {"status": "none"},
        "sample_sufficiency": {"status": "sufficient"},
        "independence": {"status": "independent"},
        "leakage": {"status": "clear"},
        "multiplicity": {"status": "complete"},
        "repeated_looks": {"status": "none"},
        "power": {"status": "sufficient"},
        "overall_diagnostics": {"status": "fail"},
        "comparisons": [comparison],
    }
    return {
        "schema_version": "audience-panel-validation-package-v1",
        "status": "valid",
        "panel_binding": deepcopy(panel_binding),
        "claim_kind": "negative",
        "package_manifest_sha256": digest(
            f"manifest-{sequence}", prefixed=False
        ),
        "package_zip_sha256": digest(f"package-{sequence}", prefixed=False),
        "evaluation": evaluation,
        "claim": None,
    }


def positive_fresh_validation(
    candidate_binding: dict[str, object], *, overlap: bool = False,
) -> dict[str, object]:
    study_id = "study-01" if overlap else "fresh-study-01"
    source = digest("source-1" if overlap else "fresh-source")
    evaluation = {
        "evaluation_id": "fresh-evaluation",
        "evaluation_sha256": digest("fresh-evaluation"),
        "evaluated_at": "2026-09-20T00:00:00Z",
        "decision": {"status": "tier4_supported"},
        "gate_results": {"all_required_gates_passed": True},
        "comparisons": [{
            "block_binding": {"block_id": "fresh-block", "study_id": study_id},
            "observations": [{
                "source": {"source_sha256": source},
                "outcome_accessed_at": "2026-09-18T00:00:00Z",
            }],
        }],
    }
    claim = {
        "claim_id": "fresh-evaluation-claim",
        "claim_sha256": digest("fresh-claim"),
        "status": "active",
        "issued_at": "2026-09-20T00:00:00Z",
        "expires_at": "2027-01-01T00:00:00Z",
    }
    return {
        "schema_version": "audience-panel-validation-package-v1",
        "status": "valid",
        "panel_binding": deepcopy(candidate_binding),
        "claim_kind": "claim",
        "package_manifest_sha256": digest("fresh-manifest", prefixed=False),
        "package_zip_sha256": digest("fresh-package", prefixed=False),
        "evaluation": evaluation,
        "claim": claim,
    }


def complete_candidate() -> tuple[dict[str, object], dict[str, object]]:
    panel = candidate_base_panel_fixture()
    binding = base_binding(panel)
    diagnosis = diagnose_real_world_persona_behavior(
        base_panel=panel,
        base_panel_binding=binding,
        validated_packages=[
            negative_package(binding, sequence=1),
            negative_package(binding, sequence=2),
        ],
        attribute_registry=registry_fixture(),
        alternative_causes=alternative_causes(),
        target_persona_id="finance-pricing-archetype",
        target_segment_id="finance",
        diagnosis_id="real-world-diagnosis-01",
        diagnosed_at="2026-08-20T00:00:00Z",
    )
    proposal = build_real_world_persona_behavior_proposal(
        base_panel=panel,
        diagnosis=diagnosis,
        proposal_id="real-world-proposal-01",
        proposed_at="2026-08-21T00:00:00Z",
    )
    candidate = materialize_real_world_candidate(
        base_panel=panel,
        proposal=proposal,
        candidate_id="real-world-candidate-01",
        candidate_version="1.1.0",
        created_at="2026-09-01T00:00:00Z",
    )
    return candidate, diagnosis


class ExperimentalRealWorldCalibrationTests(unittest.TestCase):
    maxDiff = None

    def test_repeated_authenticated_misses_create_one_bounded_diagnosis(self):
        candidate, diagnosis = complete_candidate()
        self.assertEqual("repeatable_behavioral_miss", diagnosis["decision"])
        self.assertEqual(
            "proof_needs",
            diagnosis["selected_hypothesis"]["target_persona_field"],
        )
        self.assertEqual(2, len(diagnosis["evidence_projection"]["entries"]))
        self.assertEqual(
            ["study-01", "study-02"],
            sorted(
                study
                for entry in diagnosis["evidence_projection"]["entries"]
                for study in entry["study_ids"]
            ),
        )
        self.assertEqual("1.1.0", candidate["candidate_panel"]["version"])

    def test_real_world_attribute_route_reuses_the_existing_registry_builder(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "input.json"
            output = root / "registry.json"
            source.write_bytes(canonical_json_bytes(creative_attribute_inputs()))
            command = [
                sys.executable,
                str(
                    PANEL_SCRIPTS
                    / "register-real-world-creative-attributes.py"
                ),
                "--input",
                str(source),
                "--output",
                str(output),
            ]
            completed = subprocess.run(
                command, check=False, capture_output=True, text=True
            )
            self.assertEqual(0, completed.returncode, completed.stderr)
            self.assertEqual(
                canonical_json_bytes(registry_fixture()), output.read_bytes()
            )
            replay = subprocess.run(
                command, check=False, capture_output=True, text=True
            )
            self.assertEqual(2, replay.returncode)
            self.assertEqual(
                canonical_json_bytes(registry_fixture()), output.read_bytes()
            )

    def test_candidate_changes_only_one_behavior_field_propagation_and_provenance(self):
        candidate, _diagnosis = complete_candidate()
        base = candidate["base_panel"]
        updated = candidate["candidate_panel"]
        self.assertEqual(
            canonical_json_bytes(base),
            canonical_json_bytes(candidate_base_panel_fixture()),
        )
        paths = {
            row["path"] for row in candidate["persona_behavior_diff"]["changes"]
        }
        self.assertIn(
            "$.persona_archetypes[finance-pricing-archetype].proof_needs", paths
        )
        self.assertTrue(any(path.startswith("$.calibration_history[") for path in paths))
        self.assertTrue(_requires_experimental_c2_gate(updated))
        self.assertEqual(
            CALIBRATION_HISTORY_ACTION,
            updated["calibration_history"][-1]["action"],
        )
        self.assertFalse(candidate["candidate_binding"]["registration_permitted"])

    def test_contrary_or_uncleared_evidence_cannot_create_proposal(self):
        panel = candidate_base_panel_fixture()
        binding = base_binding(panel)
        for packages, causes in (
            (
                [
                    negative_package(binding, sequence=1),
                    negative_package(binding, sequence=2, contrary=True),
                ],
                alternative_causes(),
            ),
            (
                [
                    negative_package(binding, sequence=1),
                    negative_package(binding, sequence=2),
                ],
                alternative_causes(uncleared="tracking"),
            ),
        ):
            diagnosis = diagnose_real_world_persona_behavior(
                base_panel=panel,
                base_panel_binding=binding,
                validated_packages=packages,
                attribute_registry=registry_fixture(),
                alternative_causes=causes,
                target_persona_id="finance-pricing-archetype",
                target_segment_id="finance",
                diagnosis_id="blocked-diagnosis",
                diagnosed_at="2026-08-20T00:00:00Z",
            )
            with self.assertRaises(ContractError):
                build_real_world_persona_behavior_proposal(
                    base_panel=panel,
                    diagnosis=diagnosis,
                    proposal_id="blocked-proposal",
                    proposed_at="2026-08-21T00:00:00Z",
                )

    def test_diagnostic_studies_and_source_bytes_must_be_disjoint(self):
        panel = candidate_base_panel_fixture()
        binding = base_binding(panel)
        duplicate_source = negative_package(binding, sequence=2)
        duplicate_source["evaluation"]["comparisons"][0]["observations"][0][
            "source"
        ]["source_sha256"] = digest("source-1")
        with self.assertRaisesRegex(ContractError, "disjoint outcome source"):
            diagnose_real_world_persona_behavior(
                base_panel=panel,
                base_panel_binding=binding,
                validated_packages=[
                    negative_package(binding, sequence=1),
                    duplicate_source,
                ],
                attribute_registry=registry_fixture(),
                alternative_causes=alternative_causes(),
                target_persona_id="finance-pricing-archetype",
                target_segment_id="finance",
                diagnosis_id="overlap-diagnosis",
                diagnosed_at="2026-08-20T00:00:00Z",
            )

    def test_bundle_is_new_closed_and_explicitly_experimental(self):
        candidate, diagnosis = complete_candidate()
        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary) / "candidate"
            publish_real_world_candidate_bundle(
                materialized=candidate,
                diagnosis=diagnosis,
                attribute_registry=registry_fixture(),
                alternative_causes=alternative_causes(),
                output_dir=target,
            )
            self.assertIn(
                "EXPERIMENTAL REAL-WORLD PANEL CALIBRATION CANDIDATE",
                (target / "README.txt").read_text(),
            )
            self.assertFalse((target / "audience-panel-package-v3.zip").exists())
            before = {
                path.name: path.read_bytes() for path in target.iterdir()
            }
            with self.assertRaises(ContractError):
                publish_real_world_candidate_bundle(
                    materialized=candidate,
                    diagnosis=diagnosis,
                    attribute_registry=registry_fixture(),
                    alternative_causes=alternative_causes(),
                    output_dir=target,
                )
            self.assertEqual(
                before, {path.name: path.read_bytes() for path in target.iterdir()}
            )

    def test_fresh_nonoverlapping_c1_and_exact_human_approval_gate_registration(self):
        candidate, _diagnosis = complete_candidate()
        panel_binding = {
            **{
                key: candidate["candidate_binding"]["candidate_panel_binding"][key]
                for key in ("panel_id", "panel_version", "panel_sha256")
            },
            "package_sha256": digest("candidate-package"),
        }
        fresh = positive_fresh_validation(panel_binding)
        proposal = build_registration_proposal(
            candidate=candidate,
            candidate_package_binding=panel_binding,
            fresh_validation=fresh,
            registered_at="2026-10-01T00:00:00Z",
        )
        package_sha = panel_binding["package_sha256"].removeprefix("sha256:")
        panel_sha = panel_binding["panel_sha256"].removeprefix("sha256:")
        state = {
            "schema_version": "panel-workflow-state-v1",
            "workflow_id": "c2-registration-workflow",
            "panel_id": panel_binding["panel_id"],
            "panel_version": panel_binding["panel_version"],
            "state": "approved",
            "updated_at": "2026-09-25T00:00:00Z",
            "approvals": [
                {
                    "scope": scope,
                    "status": "approved",
                    "approved_by": "human-reviewer",
                    "approved_at": "2026-09-25T00:00:00Z",
                    "target_sha256": target,
                    "note": "Reviewed exact C2 evidence and candidate.",
                }
                for scope, target in (
                    ("evidence_synthesis", digest("e", prefixed=False)),
                    ("panel_construction", digest("p", prefixed=False)),
                    (
                        "calibration",
                        proposal["registration_proposal_sha256"].removeprefix(
                            "sha256:"
                        ),
                    ),
                    ("package_registration", package_sha),
                )
            ],
            "bindings": {
                "brief_sha256": digest("b", prefixed=False),
                "panel_sha256": panel_sha,
                "report_inputs_sha256": digest("r", prefixed=False),
                "audit_sha256": digest("a", prefixed=False),
                "package_sha256": package_sha,
            },
        }
        approvals = require_registration_approval(
            workflow_state=state,
            registration_proposal=proposal,
        )
        self.assertEqual("human-reviewer", approvals["calibration"]["approved_by"])

        stale = deepcopy(state)
        next(
            row for row in stale["approvals"] if row["scope"] == "calibration"
        )["target_sha256"] = digest("stale", prefixed=False)
        with self.assertRaises(ContractError):
            require_registration_approval(
                workflow_state=stale, registration_proposal=proposal
            )

    def test_overlap_between_diagnosis_and_candidate_holdout_fails(self):
        candidate, _diagnosis = complete_candidate()
        panel_binding = {
            **{
                key: candidate["candidate_binding"]["candidate_panel_binding"][key]
                for key in ("panel_id", "panel_version", "panel_sha256")
            },
            "package_sha256": digest("candidate-package"),
        }
        with self.assertRaisesRegex(ContractError, "must not overlap"):
            build_registration_proposal(
                candidate=candidate,
                candidate_package_binding=panel_binding,
                fresh_validation=positive_fresh_validation(
                    panel_binding, overlap=True
                ),
                registered_at="2026-10-01T00:00:00Z",
            )

    def test_standard_library_registration_rejects_c2_marker_without_mutation(self):
        fixtures = ROOT / "conformance" / "fixtures" / "audience-research"
        import json

        brief = json.loads((fixtures / "approved-brief.json").read_text())
        panel = json.loads((fixtures / "approved-panel.json").read_text())
        panel["version"] = "1.1.0"
        panel["calibration_history"].append({
            "date": "2026-09-01T00:00:00Z",
            "source_type": "authenticated_real_world_outcome_validation",
            "mapped_run_id": "c2-test",
            "mapped_variants": [],
            "mapped_segments": ["finance"],
            "objective": "Experimental bounded persona-behavior calibration",
            "time_window": "2026-08",
            "data_quality": "Authenticated C1 evidence.",
            "directional_alignment": "Experimental hypothesis only.",
            "action": CALIBRATION_HISTORY_ACTION,
            "what_was_learned": "One bounded experimental hypothesis.",
            "next_run_guidance": "Use the gated C2 route.",
        })
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            package = build_audience_package(brief, panel, root / "package")
            library = root / "library"
            with self.assertRaisesRegex(
                PackageValidationError, "require fresh held-out validation"
            ):
                register_package(
                    package.package_zip_path, library_root=library
                )
            self.assertFalse(library.exists())

    def test_gated_registration_adds_new_version_and_preserves_original_bytes(self):
        import json

        fixtures = ROOT / "conformance" / "fixtures" / "audience-research"
        brief = json.loads((fixtures / "approved-brief.json").read_text())
        base_panel = json.loads((fixtures / "approved-panel.json").read_text())
        candidate_panel = deepcopy(base_panel)
        candidate_panel["version"] = "1.1.0"
        candidate_panel["calibration_history"].append({
            "date": "2026-09-01T00:00:00Z",
            "source_type": "authenticated_real_world_outcome_validation",
            "mapped_run_id": "c2-gated-test",
            "mapped_variants": [],
            "mapped_segments": ["finance"],
            "objective": "Experimental bounded persona-behavior calibration",
            "time_window": "2026-08",
            "data_quality": "Authenticated C1 evidence.",
            "directional_alignment": "Experimental hypothesis only.",
            "action": CALIBRATION_HISTORY_ACTION,
            "what_was_learned": "One bounded experimental hypothesis.",
            "next_run_guidance": "Use the gated C2 route.",
        })
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            library = root / "library"
            base_package = build_audience_package(
                brief, base_panel, root / "base-package"
            )
            candidate_package = build_audience_package(
                brief, candidate_panel, root / "candidate-package"
            )
            register_package(base_package.package_zip_path, library_root=library)
            stored_base = find_package(
                base_panel["panel_id"], base_panel["version"],
                library_root=library,
            )
            original_bytes = stored_base.read_bytes()
            package_binding, _validation, _panel = (
                read_authenticated_panel_snapshot(
                    candidate_package.package_zip_path
                )
            )
            template_candidate, _diagnosis = complete_candidate()
            proposal = {
                "schema_version": (
                    "experimental-real-world-panel-registration-proposal-v1"
                ),
                "experimental_status": "experimental",
                "registered_at": "2026-10-01T00:00:00Z",
                "candidate_binding": {
                    "candidate_id": "c2-gated-test",
                    "candidate_binding_sha256": digest("candidate-binding"),
                },
                "candidate_package_binding": package_binding,
                "diagnostic_evidence_projection_sha256": digest("projection"),
                "fresh_validation_binding": {
                    "package_sha256": digest("fresh-package"),
                    "package_manifest_sha256": digest("fresh-manifest"),
                    "evaluation_id": "fresh-evaluation",
                    "evaluation_sha256": digest("fresh-evaluation"),
                    "evaluated_at": "2026-09-20T00:00:00Z",
                    "claim_id": "fresh-claim",
                    "claim_sha256": digest("fresh-claim"),
                },
                "fresh_evidence_disjoint": True,
                "all_evidence_gates_passed": True,
                "explicit_human_approval_required": True,
                "claim_boundary": template_candidate["proposal"][
                    "claim_boundary"
                ],
                "registration_proposal_sha256": None,
            }
            proposal["registration_proposal_sha256"] = sha256_json(proposal)
            package_sha = package_binding["package_sha256"].removeprefix(
                "sha256:"
            )
            workflow = {
                "schema_version": "panel-workflow-state-v1",
                "workflow_id": "c2-gated-registration-test",
                "panel_id": package_binding["panel_id"],
                "panel_version": package_binding["panel_version"],
                "state": "approved",
                "updated_at": "2026-09-25T00:00:00Z",
                "approvals": [
                    {
                        "scope": scope,
                        "status": "approved",
                        "approved_by": "human-reviewer",
                        "approved_at": "2026-09-25T00:00:00Z",
                        "target_sha256": target,
                        "note": "Reviewed exact C2 evidence and candidate.",
                    }
                    for scope, target in (
                        ("evidence_synthesis", digest("e", prefixed=False)),
                        ("panel_construction", digest("p", prefixed=False)),
                        (
                            "calibration",
                            proposal["registration_proposal_sha256"].removeprefix(
                                "sha256:"
                            ),
                        ),
                        ("package_registration", package_sha),
                    )
                ],
                "bindings": {
                    "brief_sha256": digest("b", prefixed=False),
                    "panel_sha256": package_binding[
                        "panel_sha256"
                    ].removeprefix("sha256:"),
                    "report_inputs_sha256": digest("r", prefixed=False),
                    "audit_sha256": digest("a", prefixed=False),
                    "package_sha256": package_sha,
                },
            }
            result = register_real_world_calibrated_package(
                candidate_package.package_zip_path,
                library_root=library,
                registration_proposal=proposal,
                workflow_state=workflow,
            )
            self.assertEqual("registered", result["status"])
            self.assertEqual(original_bytes, stored_base.read_bytes())
            self.assertEqual(
                ["1.0.0", "1.1.0"],
                [row["version"] for row in list_panels(
                    library_root=library
                )["panels"]],
            )

            mismatched = deepcopy(proposal)
            mismatched["candidate_package_binding"]["package_sha256"] = digest(
                "different-package"
            )
            mismatched["registration_proposal_sha256"] = None
            mismatched["registration_proposal_sha256"] = sha256_json(mismatched)
            mismatched_workflow = deepcopy(workflow)
            next(
                row for row in mismatched_workflow["approvals"]
                if row["scope"] == "calibration"
            )["target_sha256"] = mismatched[
                "registration_proposal_sha256"
            ].removeprefix("sha256:")
            next(
                row for row in mismatched_workflow["approvals"]
                if row["scope"] == "package_registration"
            )["target_sha256"] = mismatched[
                "candidate_package_binding"
            ]["package_sha256"].removeprefix("sha256:")
            mismatched_workflow["bindings"]["package_sha256"] = mismatched[
                "candidate_package_binding"
            ]["package_sha256"].removeprefix("sha256:")
            with self.assertRaisesRegex(
                PackageValidationError, "approved candidate package"
            ):
                register_real_world_calibrated_package(
                    candidate_package.package_zip_path,
                    library_root=library,
                    registration_proposal=mismatched,
                    workflow_state=mismatched_workflow,
                )


if __name__ == "__main__":
    unittest.main()
