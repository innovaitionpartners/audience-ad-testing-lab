"""Exact materialization and production-isolation tests for sandbox candidates."""

from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
PANEL_SCRIPTS = ROOT / "skills" / "audience-panel-builder" / "scripts"
AD_LAB_SCRIPTS = ROOT / "skills" / "audience-ad-testing-lab" / "scripts"
sys.path[:0] = [str(PANEL_SCRIPTS), str(AD_LAB_SCRIPTS)]

from audience_panel_builder.common import ContractError, canonical_json_bytes, sha256_json  # noqa: E402
from audience_panel_builder.population.experimental_calibration.candidate import (  # noqa: E402
    CandidateNotMaterializable,
    UnsafeCandidateOutput,
    build_persona_authoring_projection,
    materialize_sandbox_candidate,
    publish_sandbox_candidate_bundle,
)
from audience_panel_builder.population.experimental_calibration.contracts import (  # noqa: E402
    validate_persona_authoring_projection,
    validate_sandbox_candidate_binding,
)
from audience_lab.audience_library import (  # noqa: E402
    LibraryError,
    register_package,
)
from audience_lab.audience_package import build_audience_package  # noqa: E402
from audience_lab.audience_research_v3 import validate_saved_panel_v3  # noqa: E402
from conformance.experimental_calibration_fixtures import (  # noqa: E402
    candidate_base_panel_fixture,
    candidate_proposal_inputs_fixture,
    candidate_proposal_fixture,
    digest,
    rehash,
    valid_candidate_inputs,
)


CLI = PANEL_SCRIPTS / "materialize-experimental-persona-candidate.py"
V2_FIXTURES = ROOT / "conformance" / "fixtures" / "audience-research"


def _persona(panel: dict[str, object], persona_id: str) -> dict[str, object]:
    return next(
        row
        for row in panel["persona_archetypes"]
        if row["persona_archetype_id"] == persona_id
    )


def _tree_bytes(root: Path) -> dict[str, bytes]:
    if not root.exists():
        return {}
    return {
        str(path.relative_to(root)): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def _publish_candidate_bundle(
    *,
    materialized: dict[str, object],
    output_dir: Path,
    inputs: dict[str, object] | None = None,
) -> Path:
    frozen = valid_candidate_inputs() if inputs is None else inputs
    return publish_sandbox_candidate_bundle(
        materialized=materialized,
        study_manifest=frozen["study_manifest"],
        scenario_manifests=frozen["scenario_manifests"],
        experiment_designs=frozen["experiment_designs"],
        diagnosis=frozen["diagnosis"],
        attribute_registry=frozen["attribute_registry"],
        evidence_library_snapshot=frozen["evidence_library_snapshot"],
        evidence_head_receipt=frozen["evidence_head_receipt"],
        alternative_causes=frozen["alternative_causes"],
        output_dir=output_dir,
    )


class ExperimentalCalibrationCandidateTests(unittest.TestCase):
    maxDiff = None

    def test_valid_proposal_materializes_complete_newer_panel(self):
        inputs = valid_candidate_inputs()
        before = canonical_json_bytes(inputs["base_panel"])
        result = materialize_sandbox_candidate(**inputs)
        candidate = result["candidate_panel"]
        self.assertEqual("1.1.0", candidate["version"])
        self.assertEqual(
            ["Quantified payback and implementation-risk evidence"],
            _persona(candidate, "finance-pricing-archetype")["proof_needs"],
        )
        self.assertEqual(before, canonical_json_bytes(inputs["base_panel"]))
        self.assertEqual(candidate, validate_saved_panel_v3(candidate))
        validate_sandbox_candidate_binding(result["candidate_binding"])

    def test_every_unrelated_persona_and_structural_field_is_exact(self):
        result = materialize_sandbox_candidate(**valid_candidate_inputs())
        diff_paths = {
            row["path"] for row in result["persona_behavior_diff"]["changes"]
        }
        expected = {
            "$.version",
            "$.created_at",
            "$.updated_at",
            "$.persona_archetypes[finance-pricing-archetype].proof_needs",
        }
        expected.update(
            "$.grounded_context_profiles"
            f"[{row['grounded_profile_id']}].profile_snapshot.proof_needs"
            for row in result["candidate_panel"]["grounded_context_profiles"]
        )
        self.assertEqual(expected, diff_paths)
        self.assertEqual(
            expected,
            set(result["candidate_binding"]["allowed_diff"]["changed_paths"]),
        )

    def test_applied_operation_derives_exact_before_and_snapshot_hash(self):
        inputs = valid_candidate_inputs()
        result = materialize_sandbox_candidate(**inputs)
        persona = _persona(inputs["base_panel"], "finance-pricing-archetype")
        snapshot = {
            field: deepcopy(persona[field])
            for field in (
                "anxieties",
                "decision_context",
                "motivations",
                "proof_needs",
                "role_context",
            )
        }
        operation = result["candidate_binding"]["applied_operation"]
        self.assertEqual(
            {"proof_needs": ["Pricing and returns mechanism"]},
            operation["before"],
        )
        self.assertEqual(
            sha256_json(snapshot),
            operation["target_persona_snapshot_sha256"],
        )
        self.assertEqual(
            result["base_authoring_projection"]["projection_sha256"],
            result["candidate_binding"]["base_authoring_projection_binding"][
                "projection_sha256"
            ],
        )

    def test_projection_is_internal_complete_and_candidate_change_matches(self):
        inputs = valid_candidate_inputs()
        result = materialize_sandbox_candidate(**inputs)
        base_projection = validate_persona_authoring_projection(
            result["base_authoring_projection"]
        )
        candidate_projection = validate_persona_authoring_projection(
            result["candidate_authoring_projection"]
        )
        self.assertEqual(
            len(inputs["base_panel"]["grounded_context_profiles"]),
            len(base_projection["grounded_profile_snapshot_bindings"]),
        )
        self.assertEqual(
            ["Pricing and returns mechanism"],
            base_projection["persona_archetypes"][0]["proof_needs"],
        )
        self.assertEqual(
            ["Quantified payback and implementation-risk evidence"],
            candidate_projection["persona_archetypes"][0]["proof_needs"],
        )
        self.assertTrue(
            all(
                row["profile_snapshot"]["proof_needs"]
                == ["Quantified payback and implementation-risk evidence"]
                for row in candidate_projection[
                    "grounded_profile_snapshot_bindings"
                ]
            )
        )

    def test_no_change_abstain_and_invalid_create_no_candidate(self):
        inputs = valid_candidate_inputs()
        no_change_inputs = valid_candidate_inputs(scenario_id="null-effect")
        self.assertEqual("no_change", no_change_inputs["proposal"]["proposal_type"])
        with self.assertRaises(CandidateNotMaterializable):
            materialize_sandbox_candidate(**no_change_inputs)

        for decision in ("non_identifiable", "invalid_evidence"):
            invalid = deepcopy(inputs["proposal"])
            invalid["diagnosis"]["decision"] = decision
            invalid = rehash(invalid, "proposal_sha256")
            with self.assertRaises(ContractError):
                materialize_sandbox_candidate(
                    **{**inputs, "proposal": invalid}
                )
        no_op = deepcopy(inputs["proposal"])
        no_op["operation"]["proposed_after"] = {
            "proof_needs": ["Pricing and returns mechanism"]
        }
        no_op = rehash(no_op, "proposal_sha256")
        with self.assertRaises(ContractError):
            materialize_sandbox_candidate(
                **{**inputs, "proposal": no_op}
            )

    def test_task5_intent_rejects_caller_authored_before_or_projection_claim(self):
        inputs = valid_candidate_inputs()
        for key, value in (
            ("before", {"proof_needs": ["caller value"]}),
            ("target_persona_snapshot_sha256", digest("1")),
            ("authoring_projection_sha256", digest("2")),
        ):
            proposal = deepcopy(inputs["proposal"])
            proposal["operation"][key] = value
            proposal = rehash(proposal, "proposal_sha256")
            with self.assertRaisesRegex(ContractError, "unknown fields"):
                materialize_sandbox_candidate(
                    **{**inputs, "proposal": proposal}
                )

    def test_resealed_task5_safe_action_cannot_replace_recomputed_proposal(self):
        inputs = valid_candidate_inputs()
        variants = []

        proposed = deepcopy(inputs["proposal"])
        proposed["operation"]["proposed_after"]["proof_needs"] = [
            "Caller-invented persona behavior"
        ]
        variants.append(rehash(proposed, "proposal_sha256"))

        operation = deepcopy(inputs["proposal"])
        operation["operation"]["hypothesis_id"] = "caller-hypothesis"
        variants.append(rehash(operation, "proposal_sha256"))

        diagnosis = deepcopy(inputs["proposal"])
        diagnosis["diagnosis"]["diagnosis_sha256"] = digest("4")
        variants.append(rehash(diagnosis, "proposal_sha256"))

        registry = deepcopy(inputs["proposal"])
        registry["operation"]["creative_attribute_registry_sha256"] = digest("5")
        variants.append(rehash(registry, "proposal_sha256"))

        safe_action = deepcopy(inputs["proposal"])
        safe_action["proposal_type"] = "no_change"
        safe_action["operation"] = None
        safe_action["diagnosis"]["decision"] = "no_repeatable_miss"
        safe_action["expected_effect"] = {
            "direction": "none",
            "claim_boundary": "no_change_supported_in_fixture",
        }
        safe_action["uncertainty"]["monte_carlo_standard_error"] = None
        variants.append(rehash(safe_action, "proposal_sha256"))

        for proposal in variants:
            with self.subTest(proposal=proposal["proposal_sha256"]):
                with self.assertRaisesRegex(
                    ContractError, "recomputed|byte-match"
                ):
                    materialize_sandbox_candidate(
                        **{**inputs, "proposal": proposal}
                    )

        frozen = deepcopy(inputs)
        frozen["diagnosis"]["diagnosis_sha256"] = digest("6")
        with self.assertRaises(ContractError):
            materialize_sandbox_candidate(**frozen)
        frozen = deepcopy(inputs)
        frozen["attribute_registry"]["registry_sha256"] = digest("7")
        with self.assertRaises(ContractError):
            materialize_sandbox_candidate(**frozen)

    def test_permission_and_stale_base_bindings_fail_closed(self):
        inputs = valid_candidate_inputs()
        proposal = deepcopy(inputs["proposal"])
        proposal["sandbox_candidate_materialization_permitted"] = False
        proposal = rehash(proposal, "proposal_sha256")
        with self.assertRaises(ContractError):
            materialize_sandbox_candidate(**{**inputs, "proposal": proposal})
        for field, value in (
            ("panel_id", "other-panel"),
            ("panel_version", "9.9.9"),
            ("panel_sha256", digest("1")),
            ("persona_snapshot_sha256", digest("2")),
        ):
            stale = deepcopy(inputs["proposal"])
            stale["base_panel_binding"][field] = value
            stale = rehash(stale, "proposal_sha256")
            with self.assertRaisesRegex(
                ContractError,
                "recomputed|base panel binding",
            ):
                materialize_sandbox_candidate(
                    **{**inputs, "proposal": stale}
                )

    def test_candidate_version_must_be_strictly_newer_semver(self):
        inputs = valid_candidate_inputs()
        for version in ("1.0.0", "0.9.9", "1.0", "01.1.0", "v2.0.0"):
            with self.subTest(version=version):
                with self.assertRaisesRegex(
                    ContractError, "candidate_version|strictly newer"
                ):
                    materialize_sandbox_candidate(
                        **{**inputs, "candidate_version": version}
                    )

    def test_candidate_timestamp_is_strictly_later_and_sets_both_fields(self):
        inputs = valid_candidate_inputs()
        base = inputs["base_panel"]
        self.assertLess(base["created_at"], inputs["created_at"])
        self.assertLess(base["updated_at"], inputs["created_at"])
        candidate = materialize_sandbox_candidate(**inputs)["candidate_panel"]
        self.assertEqual(inputs["created_at"], candidate["created_at"])
        self.assertEqual(inputs["created_at"], candidate["updated_at"])
        for timestamp in (
            base["created_at"],
            base["updated_at"],
            "2026-01-01T00:00:00Z",
        ):
            with self.subTest(timestamp=timestamp):
                with self.assertRaisesRegex(ContractError, "strictly later"):
                    materialize_sandbox_candidate(
                        **{**inputs, "created_at": timestamp}
                    )

    def test_all_five_behavior_field_shapes_materialize(self):
        inputs = valid_candidate_inputs()
        values = {
            "anxieties": ["New fictional anxiety"],
            "decision_context": "New fictional decision context",
            "motivations": ["New fictional motivation"],
            "proof_needs": ["New fictional proof need"],
            "role_context": "New fictional role context",
        }
        for field, proposed_value in values.items():
            with self.subTest(field=field):
                proposal = deepcopy(inputs["proposal"])
                proposal["operation"]["changed_fields"] = [field]
                proposal["operation"]["proposed_after"] = {
                    field: proposed_value
                }
                proposal = rehash(proposal, "proposal_sha256")
                field_inputs = {**inputs, "proposal": proposal}
                with patch(
                    "audience_panel_builder.population.experimental_calibration"
                    ".candidate.build_experimental_proposal",
                    return_value=proposal,
                ):
                    result = materialize_sandbox_candidate(**field_inputs)
                persona = _persona(
                    result["candidate_panel"],
                    "finance-pricing-archetype",
                )
                self.assertEqual(proposed_value, persona[field])
                self.assertTrue(
                    all(
                        row["profile_snapshot"][field] == proposed_value
                        for row in result["candidate_panel"][
                            "grounded_context_profiles"
                        ]
                    )
                )

    def test_unknown_persona_and_zero_matching_profiles_fail(self):
        inputs = valid_candidate_inputs()
        unknown = deepcopy(inputs["proposal"])
        unknown["operation"]["target_persona_id"] = "unknown-persona"
        unknown["base_panel_binding"]["persona_id"] = "unknown-persona"
        unknown = rehash(unknown, "proposal_sha256")
        with self.assertRaisesRegex(ContractError, "recomputed|target persona"):
            materialize_sandbox_candidate(
                **{**inputs, "proposal": unknown}
            )

        no_profiles = deepcopy(inputs["base_panel"])
        for row in no_profiles["grounded_context_profiles"]:
            row["persona_archetype_id"] = "other-persona"
        proposal = candidate_proposal_fixture(no_profiles)
        with self.assertRaises(ContractError):
            materialize_sandbox_candidate(
                **{**inputs, "base_panel": no_profiles, "proposal": proposal}
            )

    def test_duplicate_profile_stale_snapshot_and_partial_update_fail(self):
        inputs = valid_candidate_inputs()
        duplicate = deepcopy(inputs["base_panel"])
        duplicate["grounded_context_profiles"][1]["grounded_profile_id"] = (
            duplicate["grounded_context_profiles"][0]["grounded_profile_id"]
        )
        with self.assertRaises((ContractError, ValueError)):
            materialize_sandbox_candidate(
                **{
                    **inputs,
                    "base_panel": duplicate,
                    "proposal": candidate_proposal_fixture(duplicate),
                }
            )

        stale = deepcopy(inputs["base_panel"])
        stale["grounded_context_profiles"][0]["profile_snapshot"][
            "proof_needs"
        ] = ["Stale profile snapshot"]
        with self.assertRaises(ContractError):
            materialize_sandbox_candidate(
                **{
                    **inputs,
                    "base_panel": stale,
                    "proposal": candidate_proposal_fixture(stale),
                }
            )

    def test_bundle_is_deterministic_closed_and_non_production(self):
        result = materialize_sandbox_candidate(**valid_candidate_inputs())
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            first = _publish_candidate_bundle(
                materialized=result, output_dir=root / "first"
            )
            second = _publish_candidate_bundle(
                materialized=result, output_dir=root / "second"
            )
            self.assertEqual(_tree_bytes(first), _tree_bytes(second))
            self.assertEqual(
                {
                    "README.txt",
                    "base-persona-authoring-projection.json",
                    "base-persona-snapshot.json",
                    "bundle-manifest.json",
                    "candidate-audience-panel.json",
                    "candidate-persona-authoring-projection.json",
                    "candidate-persona-snapshot.json",
                    "experimental-candidate-binding.json",
                    "experimental-proposal.json",
                    "persona-behavior-diff.json",
                    "standalone-panel-validation.json",
                },
                set(_tree_bytes(first)),
            )
            self.assertFalse((first / "package-manifest.json").exists())
            self.assertFalse((first / "audience-panel-package.zip").exists())
            self.assertTrue(
                (first / "README.txt")
                .read_text()
                .startswith(
                    "EXPERIMENTAL SYNTHETIC-ONLY SANDBOX CANDIDATE\n"
                )
            )
            with self.assertRaises(UnsafeCandidateOutput):
                _publish_candidate_bundle(
                    materialized=result, output_dir=first
                )

    def test_publish_rejects_changed_unrelated_value_or_partial_profile_update(self):
        materialized = materialize_sandbox_candidate(
            **valid_candidate_inputs()
        )
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            unrelated = deepcopy(materialized)
            unrelated["candidate_panel"]["governance"]["allowed_uses"].append(
                "Forbidden production use"
            )
            with self.assertRaises(ContractError):
                _publish_candidate_bundle(
                    materialized=unrelated,
                    output_dir=root / "unrelated",
                )

            partial = deepcopy(materialized)
            partial["candidate_panel"]["grounded_context_profiles"][0][
                "profile_snapshot"
            ]["proof_needs"] = ["Pricing and returns mechanism"]
            with self.assertRaises(ContractError):
                _publish_candidate_bundle(
                    materialized=partial,
                    output_dir=root / "partial",
                )

            projection = deepcopy(materialized)
            projection["candidate_authoring_projection"][
                "grounded_profile_snapshot_bindings"
            ][0]["profile_snapshot"]["proof_needs"] = ["Forged projection"]
            with self.assertRaises(ContractError):
                _publish_candidate_bundle(
                    materialized=projection,
                    output_dir=root / "projection",
                )

    def test_publish_reconstructs_base_and_rejects_resealed_history_or_mutation(self):
        materialized = materialize_sandbox_candidate(
            **valid_candidate_inputs()
        )
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            invented = deepcopy(materialized)
            invented_before = ["Invented caller-authored before"]
            invented["base_persona_snapshot"]["proof_needs"] = invented_before
            for row in invented["base_authoring_projection"][
                "persona_archetypes"
            ]:
                if row["persona_archetype_id"] == "finance-pricing-archetype":
                    row["proof_needs"] = invented_before
            for row in invented["base_authoring_projection"][
                "grounded_profile_snapshot_bindings"
            ]:
                row["profile_snapshot"]["proof_needs"] = invented_before
                row["profile_snapshot_sha256"] = sha256_json(
                    row["profile_snapshot"]
                )
            invented["base_authoring_projection"] = rehash(
                invented["base_authoring_projection"], "projection_sha256"
            )
            with self.assertRaises(ContractError):
                _publish_candidate_bundle(
                    materialized=invented,
                    output_dir=root / "invented",
                )

            unrelated = deepcopy(materialized)
            _persona(
                unrelated["candidate_panel"],
                "finance-pricing-archetype",
            )["display_name"] = "Caller-mutated unrelated display name"
            rebuilt = build_persona_authoring_projection(
                validated_panel=unrelated["candidate_panel"]
            )
            unrelated["candidate_authoring_projection"] = rebuilt
            unrelated["candidate_binding"][
                "candidate_authoring_projection_binding"
            ] = {
                "projection_id": rebuilt["projection_id"],
                "projection_sha256": rebuilt["projection_sha256"],
            }
            unrelated["candidate_binding"]["candidate_panel_binding"][
                "panel_sha256"
            ] = sha256_json(unrelated["candidate_panel"])
            unrelated["candidate_binding"] = rehash(
                unrelated["candidate_binding"], "candidate_binding_sha256"
            )
            unrelated["persona_behavior_diff"]["candidate_panel_sha256"] = (
                sha256_json(unrelated["candidate_panel"])
            )
            unrelated["persona_behavior_diff"] = rehash(
                unrelated["persona_behavior_diff"], "diff_sha256"
            )
            unrelated["standalone_panel_validation"]["panel_sha256"] = (
                sha256_json(unrelated["candidate_panel"])
            )
            with self.assertRaises(ContractError):
                _publish_candidate_bundle(
                    materialized=unrelated,
                    output_dir=root / "unrelated-resealed",
                )

    def test_publish_rebuilds_complete_operation_and_candidate_binding(self):
        materialized = materialize_sandbox_candidate(
            **valid_candidate_inputs()
        )
        mutations = (
            lambda value: value["candidate_binding"]["applied_operation"].update(
                {"hypothesis_id": "caller-other-hypothesis"}
            ),
            lambda value: value["candidate_binding"]["applied_operation"].update(
                {"evidence_sha256": [digest("8")]}
            ),
            lambda value: value["candidate_binding"]["applied_operation"].update(
                {"rationale": "Caller-authored rationale."}
            ),
            lambda value: value["candidate_binding"][
                "forbidden_diff_check"
            ].update({"forbidden_paths": ["$.segments"]}),
            lambda value: value["candidate_binding"][
                "structural_validation"
            ].update({"production_workflow_state": "passed"}),
            lambda value: value["candidate_binding"].update(
                {"limitations": ["Caller-authored limitation"]}
            ),
            lambda value: value["candidate_binding"][
                "proposal_binding"
            ].update({"proposal_id": "caller-proposal"}),
        )
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for index, mutate in enumerate(mutations):
                with self.subTest(index=index):
                    changed = deepcopy(materialized)
                    mutate(changed)
                    changed["candidate_binding"] = rehash(
                        changed["candidate_binding"],
                        "candidate_binding_sha256",
                    )
                    with self.assertRaises(ContractError):
                        _publish_candidate_bundle(
                            materialized=changed,
                            output_dir=root / f"changed-{index}",
                        )

    def test_publish_recomputes_task5_before_accepting_coherent_graph(self):
        inputs = valid_candidate_inputs()
        invented_proposal = deepcopy(inputs["proposal"])
        invented_proposal["operation"]["proposed_after"]["proof_needs"] = [
            "Invented coordinated publisher-only value"
        ]
        invented_proposal = rehash(
            invented_proposal,
            "proposal_sha256",
        )
        with patch(
            "audience_panel_builder.population.experimental_calibration"
            ".candidate.build_experimental_proposal",
            return_value=invented_proposal,
        ):
            invented_graph = materialize_sandbox_candidate(
                **{**inputs, "proposal": invented_proposal}
            )

        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "invented"
            with self.assertRaises(ContractError):
                _publish_candidate_bundle(
                    materialized=invented_graph,
                    output_dir=output,
                    inputs=inputs,
                )
            self.assertFalse(output.exists())

    def test_publish_replays_candidate_timestamp_chronology(self):
        inputs = valid_candidate_inputs()
        materialized = materialize_sandbox_candidate(**inputs)
        base = inputs["base_panel"]
        attacks = (
            base["created_at"],
            base["updated_at"],
            "2026-07-01T00:00:00Z",
            "2026-07-10T00:00:00Z",
        )
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for index, timestamp in enumerate(attacks):
                with self.subTest(timestamp=timestamp):
                    changed = deepcopy(materialized)
                    panel = changed["candidate_panel"]
                    panel["created_at"] = timestamp
                    panel["updated_at"] = timestamp
                    projection = build_persona_authoring_projection(
                        validated_panel=panel
                    )
                    changed["candidate_authoring_projection"] = projection
                    binding = changed["candidate_binding"]
                    binding["created_at"] = timestamp
                    binding["candidate_panel_binding"]["panel_sha256"] = (
                        sha256_json(panel)
                    )
                    binding["candidate_authoring_projection_binding"] = {
                        "projection_id": projection["projection_id"],
                        "projection_sha256": projection["projection_sha256"],
                    }
                    diff = changed["persona_behavior_diff"]
                    diff["candidate_panel_sha256"] = sha256_json(panel)
                    for row in diff["changes"]:
                        if row["path"] in {"$.created_at", "$.updated_at"}:
                            row["after"] = timestamp
                    changed["persona_behavior_diff"] = rehash(
                        diff,
                        "diff_sha256",
                    )
                    changed["standalone_panel_validation"]["panel_sha256"] = (
                        sha256_json(panel)
                    )
                    changed["candidate_binding"] = rehash(
                        binding,
                        "candidate_binding_sha256",
                    )
                    with self.assertRaisesRegex(
                        ContractError,
                        "strictly later|proposal timestamp|candidate binding",
                    ):
                        _publish_candidate_bundle(
                            materialized=changed,
                            output_dir=root / f"timestamp-{index}",
                        )

    def test_direct_production_registration_rejects_without_library_mutation(self):
        result = materialize_sandbox_candidate(**valid_candidate_inputs())
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            bundle = _publish_candidate_bundle(
                materialized=result, output_dir=root / "candidate"
            )
            brief = json.loads(
                (V2_FIXTURES / "approved-brief.json").read_text()
            )
            panel = json.loads(
                (V2_FIXTURES / "approved-panel.json").read_text()
            )
            package = build_audience_package(brief, panel, root / "package")
            library = root / "production-library"
            register_package(package.package_zip_path, library_root=library)
            before = _tree_bytes(library)
            with self.assertRaises((LibraryError, OSError, ValueError)):
                register_package(bundle, library_root=library)
            self.assertEqual(before, _tree_bytes(library))

    def test_base_panel_remains_exact_without_package_provenance_claim(self):
        inputs = valid_candidate_inputs()
        base_before = canonical_json_bytes(inputs["base_panel"])
        result = materialize_sandbox_candidate(**inputs)
        self.assertEqual(base_before, canonical_json_bytes(inputs["base_panel"]))
        self.assertNotIn(
            "panel_package_sha256",
            result["candidate_binding"]["base_panel_binding"],
        )

    def test_cli_exit_codes_and_input_output_alias_guard(self):
        inputs = valid_candidate_inputs()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            base = root / "base.json"
            proposal = root / "proposal.json"
            base.write_bytes(canonical_json_bytes(inputs["base_panel"]))
            proposal.write_bytes(canonical_json_bytes(inputs["proposal"]))
            command = [
                sys.executable,
                str(CLI),
                "--base-panel",
                str(base),
                "--proposal",
                str(proposal),
                "--study-manifest",
                str(root / "study-manifest.json"),
                "--scenario-manifests",
                str(root / "scenario-manifests.json"),
                "--experiment-designs",
                str(root / "experiment-designs.json"),
                "--diagnosis",
                str(root / "diagnosis.json"),
                "--attribute-registry",
                str(root / "attribute-registry.json"),
                "--evidence-library-snapshot",
                str(root / "evidence-library-snapshot.json"),
                "--evidence-head-receipt",
                str(root / "evidence-head-receipt.json"),
                "--alternative-causes",
                str(root / "alternative-causes.json"),
                "--candidate-id",
                inputs["candidate_id"],
                "--candidate-version",
                inputs["candidate_version"],
                "--created-at",
                inputs["created_at"],
                "--output-dir",
                str(root / "candidate"),
            ]
            for key, filename in (
                ("study_manifest", "study-manifest.json"),
                ("scenario_manifests", "scenario-manifests.json"),
                ("experiment_designs", "experiment-designs.json"),
                ("diagnosis", "diagnosis.json"),
                ("attribute_registry", "attribute-registry.json"),
                ("evidence_library_snapshot", "evidence-library-snapshot.json"),
                ("evidence_head_receipt", "evidence-head-receipt.json"),
                ("alternative_causes", "alternative-causes.json"),
            ):
                (root / filename).write_bytes(
                    canonical_json_bytes(inputs[key])
                )
            completed = subprocess.run(
                command, capture_output=True, text=True, check=False
            )
            self.assertEqual(0, completed.returncode, completed.stderr)
            self.assertEqual(
                canonical_json_bytes(inputs["base_panel"]), base.read_bytes()
            )
            self.assertEqual(3, subprocess.run(
                command, capture_output=True, text=True, check=False
            ).returncode)
            alias_command = command[:-1] + [str(base)]
            self.assertEqual(3, subprocess.run(
                alias_command, capture_output=True, text=True, check=False
            ).returncode)

            no_change_inputs = valid_candidate_inputs(
                scenario_id="null-effect"
            )
            proposal.write_bytes(
                canonical_json_bytes(no_change_inputs["proposal"])
            )
            for key, filename in (
                ("study_manifest", "study-manifest.json"),
                ("scenario_manifests", "scenario-manifests.json"),
                ("experiment_designs", "experiment-designs.json"),
                ("diagnosis", "diagnosis.json"),
                ("attribute_registry", "attribute-registry.json"),
                ("evidence_library_snapshot", "evidence-library-snapshot.json"),
                ("evidence_head_receipt", "evidence-head-receipt.json"),
                ("alternative_causes", "alternative-causes.json"),
            ):
                (root / filename).write_bytes(
                    canonical_json_bytes(no_change_inputs[key])
                )
            no_change_command = command[:-1] + [str(root / "no-change")]
            self.assertEqual(4, subprocess.run(
                no_change_command,
                capture_output=True,
                text=True,
                check=False,
            ).returncode)

            proposal.write_text("{}")
            invalid_command = command[:-1] + [str(root / "invalid")]
            self.assertEqual(2, subprocess.run(
                invalid_command,
                capture_output=True,
                text=True,
                check=False,
            ).returncode)


if __name__ == "__main__":
    unittest.main()
