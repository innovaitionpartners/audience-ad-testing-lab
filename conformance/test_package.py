"""Conformance checks for the public Audience Ad Testing Lab package."""

from __future__ import annotations

import ast
import copy
import json
from pathlib import Path
import re
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]
SKILL_ROOT = ROOT / "skills" / "audience-ad-testing-lab"
sys.path.insert(0, str(SKILL_ROOT / "scripts"))

from audience_lab.contracts import SUPPORTED_CREATIVE_FORMATS, ValidityStatus  # noqa: E402
from audience_lab.dashboard import APPROVED_ROSTER_STATES, SUPPORTED_METHODS  # noqa: E402
from audience_lab.planning import StudyRequest  # noqa: E402
from audience_lab.responses import (  # noqa: E402
    RESPONSE_VALIDATORS,
    validate_job,
    validate_response,
)


METADATA_FILES = (
    ".claude-plugin/marketplace.json",
    ".claude-plugin/plugin.json",
    ".codex-plugin/plugin.json",
    "gemini-extension.json",
)
PUBLIC_GLOBS = (
    "README.md",
    "skills/audience-data-lab/SKILL.md",
    "skills/audience-data-lab/agents/*.yaml",
    "skills/audience-data-lab/references/*.md",
    "skills/audience-ad-testing-lab/SKILL.md",
    "skills/audience-ad-testing-lab/agents/*.md",
    "skills/audience-ad-testing-lab/references/*.md",
    "skills/audience-panel-builder/SKILL.md",
    "skills/audience-panel-builder/agents/*.yaml",
    "skills/audience-panel-builder/references/*.md",
    "skills/real-world-outcome-data-prep/SKILL.md",
    "skills/real-world-outcome-data-prep/agents/*.yaml",
    "skills/real-world-outcome-data-prep/references/*.md",
    *METADATA_FILES,
)
RECOVERY_CONFIG_KEYS = {
    "version",
    "calibration_status",
    "library_size_bands",
    "shortlist_size_bands",
    "segment_count",
    "tie_inability_band",
    "utility_separation_band",
    "planned_participation_floor",
    "usable_participation_floor",
    "bootstrap_count",
    "successful_fit_floor",
    "shortlist_thresholds",
}
DENOMINATOR_NAMES = {
    "total_model_calls",
    "accepted_response_records",
    "accepted_unique_replicates",
    "accepted_response_records_by_stage",
    "accepted_unique_replicates_by_stage",
    "unique_archetypes",
    "grounded_context_profiles",
}


def public_paths() -> tuple[Path, ...]:
    paths: set[Path] = set()
    for pattern in PUBLIC_GLOBS:
        paths.update(ROOT.glob(pattern))
    return tuple(sorted(paths))


def public_source_text() -> str:
    return "\n".join(path.read_text(encoding="utf-8") for path in public_paths())


def source(relative_path: str) -> str:
    repository_path = ROOT / relative_path
    package_path = SKILL_ROOT / relative_path
    path = repository_path if repository_path.exists() else package_path
    return path.read_text(encoding="utf-8")


def markdown_section(relative_path: str, heading: str) -> str:
    """Return one Markdown section, ending at the next peer-or-higher heading."""

    text = source(relative_path)
    marker = f"{heading}\n"
    if marker not in text:
        raise AssertionError(f"missing section {heading!r} in {relative_path}")
    section = text.split(marker, 1)[1]
    level = len(heading) - len(heading.lstrip("#"))
    next_heading = re.compile(rf"^#{{1,{level}}} .+$", re.MULTILINE)
    match = next_heading.search(section)
    return section[: match.start()] if match else section


def fenced_json(relative_path: str, heading: str) -> dict[str, object]:
    section = markdown_section(relative_path, heading)
    match = re.search(r"```json\n(.*?)\n```", section, re.DOTALL)
    if match is None:
        raise AssertionError(f"missing canonical JSON example in {relative_path} {heading}")
    payload = json.loads(match.group(1))
    if not isinstance(payload, dict):
        raise AssertionError(f"canonical JSON example must be an object: {heading}")
    return payload


def missing_tokens(text: str, required: tuple[str, ...]) -> list[str]:
    lowered = text.lower()
    return [token for token in required if token.lower() not in lowered]


def runtime_instruction_text() -> str:
    paths = [
        SKILL_ROOT / "SKILL.md",
        *sorted((SKILL_ROOT / "agents").glob("*.md")),
        *sorted((SKILL_ROOT / "references").glob("*.md")),
    ]
    return "\n".join(path.read_text(encoding="utf-8") for path in paths)


def dataclass_defaults(path: Path, class_name: str) -> dict[str, object]:
    """Read literal dataclass defaults without importing optional SciPy modules."""

    module = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in module.body:
        if isinstance(node, ast.ClassDef) and node.name == class_name:
            defaults: dict[str, object] = {}
            for statement in node.body:
                if (
                    isinstance(statement, ast.AnnAssign)
                    and isinstance(statement.target, ast.Name)
                    and statement.value is not None
                ):
                    defaults[statement.target.id] = ast.literal_eval(statement.value)
            return defaults
    raise AssertionError(f"{class_name} not found in {path}")


class PackageTests(unittest.TestCase):
    def test_marketer_display_name_preserves_internal_slug(self):
        skill = source("skills/audience-ad-testing-lab/SKILL.md")
        self.assertIn("# Ad Testing Lab", skill)
        self.assertIn("name: audience-ad-testing-lab", skill)
        self.assertNotIn("# Audience Ad Testing Lab", skill)
        self.assertTrue(SKILL_ROOT.name == "audience-ad-testing-lab")

    def test_skill_package_is_the_only_production_tree(self):
        self.assertTrue((SKILL_ROOT / "SKILL.md").is_file())
        for duplicate in (
            "SKILL.md",
            "agents",
            "assets",
            "references",
            "requirements-screening.txt",
            "scripts",
        ):
            self.assertFalse((ROOT / duplicate).exists(), duplicate)

    def test_creative_specialist_is_absent(self):
        self.assertFalse((SKILL_ROOT / "agents/creative-specialist-prompt.md").exists())
        text = public_source_text().lower()
        for stale in (
            "creative specialist",
            "creative-specialist",
            "creative_specialist",
            "creative_specialist_outputs",
        ):
            self.assertNotIn(stale, text)

    def test_prohibited_public_claims_and_terms_are_absent(self):
        text = public_source_text().lower()
        approved_release_a_value = "population_composition: not_available"
        contracts = source(
            "skills/audience-panel-builder/references/contracts.md"
        ).lower()
        approved_migration_line = '"status": "not_available",'
        v3_contracts = source(
            "skills/audience-panel-builder/references/v3-population-contracts.md"
        ).lower()
        self.assertEqual(
            1,
            sum(
                line.strip() == approved_release_a_value
                for line in contracts.splitlines()
            ),
        )
        self.assertEqual(1, text.count(approved_release_a_value))
        self.assertEqual(
            1,
            sum(
                line.strip() == approved_migration_line
                for line in v3_contracts.splitlines()
            ),
        )
        self.assertEqual(1, text.count(approved_migration_line))
        text = text.replace(approved_release_a_value, "", 1)
        text = text.replace(approved_migration_line, "", 1)
        for prohibited in (
            "independent panelist",
            "valid consensus signal",
            "confidence rating",
            "manual_visual_attention",
            "ranked synthetic preference",
            "customer preference",
            "not_requested",
            "not_available",
            "requested_unavailable",
        ):
            self.assertNotIn(prohibited, text)
        self.assertIsNone(
            re.search(
                r"\boptional(?:\s+[a-z-]+){0,3}\s+(?:saliency|sum|visual[ -]attention)\b",
                text,
            )
        )

    def test_synthetic_units_are_not_described_as_people(self):
        text = runtime_instruction_text().lower()
        self.assertIn("context-isolated synthetic replicate", text)
        self.assertIn("synthetic replicates are not people", text)
        self.assertIn("human_sample_independence: false", text)
        self.assertNotRegex(text, r"\b\d+[- ]person synthetic panel\b")

    def test_automatic_heatmap_contract_covers_every_imagery_representation(self):
        text = runtime_instruction_text()
        self.assertIn(
            "Automatically generate or import one attention heatmap for every inspectable media representation",
            text,
        )
        for creative_format in ("static_image", "carousel", "video_representation"):
            self.assertIn(creative_format, text)
        self.assertIn("`copy_only` is the sole normal omission route", text)
        self.assertIn("No imagery was tested.", text)
        self.assertIn("hard stop before dashboard rendering", text)

    def test_heatmap_evidence_is_hash_bound_timed_and_noncausal(self):
        text = (ROOT / "skills/audience-ad-testing-lab/references/visual-attention-saliency.md").read_text(encoding="utf-8")
        for field in (
            "representation_id",
            "content_hash",
            "overlay_content_hash",
            "provider",
            "method",
            "predeclared_target",
            "target_declared_at",
            "revealed_at",
            "categorical_alignment",
            "limitations",
        ):
            self.assertIn(field, text)
        self.assertIn("approved_at < revealed_at", text)
        self.assertIn("target_declared_at < revealed_at", text)
        for protected_output in (
            "screening math",
            "boundary resolution",
            "finalist shares",
            "rubric scores",
            "deterministically proposed roster",
            "approved finalist roster",
        ):
            self.assertIn(protected_output, text.lower())
        self.assertIn("human override", text.lower())
        self.assertIn("heuristic", text.lower())

    def test_dashboard_contract_is_marketer_first_with_full_methodology(self):
        text = source(
            "skills/audience-ad-testing-lab/references/synthesis-dashboard-calibration.md"
        )
        navigation = (
            "Overview, Ads tested, Test audience, All ad results, Top ads, Feedback, "
            "Attention heatmap (imagery only), AI audience responses, Methodology, Downloads"
        )
        self.assertIn(navigation, text)
        self.assertIn("Methodology/Test details", text)
        for question in (
            "what was tested",
            "what stood out",
            "which ads merit closer review or are pending",
            "how much evidence was usable",
            "the limits",
        ):
            self.assertIn(question, text.lower())
        self.assertIn("plain, literal primary labels", text.lower())
        self.assertIn("keyboard/tap information popovers", text.lower())

    def test_feedback_contract_uses_one_canonical_actionable_schema(self):
        reference = source(
            "skills/audience-ad-testing-lab/references/synthesis-dashboard-calibration.md"
        )
        prompt = source("skills/audience-ad-testing-lab/agents/arbiter-prompt.md")
        for text in (reference, prompt):
            for field in (
                "creative_id",
                "feedback_type",
                "evidence_scope",
                "theme",
                "why_it_matters",
                "recommended_action",
                "response_ids",
                "exposed_base",
                "limitations",
            ):
                self.assertIn(field, text)
        reference_schema = reference.split("## Feedback Synthesis", 1)[1].split(
            "## Marketer-First Dashboard Contract", 1
        )[0]
        prompt_schema = prompt.split('"themes": [', 1)[1].split(
            '"attention_heatmap"', 1
        )[0]
        for stale_alias in ("variation_id", "source_response_ids", '"text"', '"limits"'):
            self.assertNotIn(stale_alias, reference_schema)
            self.assertNotIn(stale_alias, prompt_schema)
        self.assertNotIn("opacity control", reference.lower())
        self.assertIn("All ad results", reference)
        self.assertIn("Top ads", reference)
        for stale_label in (
            "First-round signal",
            "How often it stayed in the cut",
            "Complete-set signal",
            "First-choice share in this finalist round",
        ):
            self.assertNotIn(stale_label, reference)
        self.assertIn("Overall result", reference)
        self.assertIn("How often it ranked among the leaders", reference)
        self.assertIn("Chosen first in the closer review", reference)

    def test_public_names_match_runtime_contracts(self):
        self.assertEqual(
            {"copy_only", "static_image", "carousel", "video_representation"},
            set(SUPPORTED_CREATIVE_FORMATS),
        )
        self.assertEqual(
            {"complete_exposure", "partial_exposure_maxdiff"},
            set(SUPPORTED_METHODS),
        )
        self.assertEqual(
            {"screening_response", "boundary_response", "finalist_response"},
            set(RESPONSE_VALIDATORS),
        )
        self.assertEqual(
            {"valid", "exploratory", "invalid", "incomplete"},
            {status.value for status in ValidityStatus},
        )
        self.assertEqual({"approved", "approved_with_override"}, set(APPROVED_ROSTER_STATES))

        text = runtime_instruction_text()
        for token in (
            *sorted(SUPPORTED_CREATIVE_FORMATS),
            *sorted(SUPPORTED_METHODS),
            *sorted(RESPONSE_VALIDATORS),
            *sorted(DENOMINATOR_NAMES),
            "valid",
            "exploratory",
            "invalid",
            "incomplete",
            "resolved",
            "unresolved",
            "awaiting_approval",
            "approved",
            "approved_with_override",
        ):
            self.assertIn(token, text)

    def test_public_cli_and_runtime_names_are_exact(self):
        text = runtime_instruction_text()
        for command in (
            "python3 scripts/plan-large-library.py",
            "python3 scripts/validate-panel-run.py",
            "python3 scripts/aggregate-screening.py screening",
            "python3 scripts/aggregate-screening.py boundary",
            "python3 scripts/render-dashboard.py",
            "python3 scripts/validate-dashboard.py",
        ):
            self.assertIn(command, text)
        self.assertIn("scripts/claude-large-panel-workflow.mjs", text)
        self.assertIn("planner must run before screening fan-out", text.lower())
        self.assertIn("aggregator must run before arbiter synthesis", text.lower())

    def test_panel_builder_public_workflow_uses_only_approval_gated_package_commands(self):
        text = "\n".join(
            source(path)
            for path in (
                "skills/audience-panel-builder/SKILL.md",
                "skills/audience-panel-builder/references/route-workflows-and-output-templates.md",
            )
        )
        for command in (
            "python3 scripts/propose-panel-package.py",
            "python3 scripts/build-approved-panel-package.py",
            "python3 scripts/register-approved-panel.py",
        ):
            self.assertIn(command, text)
            command_lines = [
                line for line in text.splitlines() if command in line
            ]
            self.assertTrue(command_lines, command)
            for line in command_lines:
                for flag in (
                    "--ledger",
                    "--finding-support",
                    "--synthesis",
                    "--report-manifest",
                ):
                    self.assertIn(flag, line)
        for legacy in (
            "python3 scripts/build-panel-package.py",
            "python3 scripts/manage-panel-library.py",
        ):
            self.assertNotIn(legacy, text)
        for phrase in (
            "calculate package proposal digest without materializing a reusable package",
            "approve `package_registration`",
            "build the canonical package",
        ):
            self.assertIn(phrase, text.lower())

    def test_method_boundaries_progressive_reveal_and_raw_returns_are_explicit(self):
        text = runtime_instruction_text().lower()
        self.assertRegex(
            text,
            r"`?complete_exposure`? never uses four-item maxdiff blocks",
        )
        self.assertRegex(
            text,
            r"`?partial_exposure_maxdiff`? uses four-item",
        )
        self.assertIn("progressive_reveal", text)
        self.assertIn("accepted and rejected raw provider returns", text)
        self.assertIn("conditional only on the approved finalist set", text)
        self.assertIn("one context-isolated synthetic replicate per worker", text)

    def test_complete_exposure_execution_is_explicit_per_file(self):
        aggregator = source("skills/audience-ad-testing-lab/scripts/aggregate-screening.py")
        self.assertIn("aggregate_complete_exposure", aggregator)
        jobs = json.loads(source("conformance/fixtures/screening-jobs-valid.json"))[
            "synthetic_replicate_jobs"
        ]
        complete_job = copy.deepcopy(jobs[0])
        complete_job["method"] = "complete_exposure"
        complete_job["shown_order"] = complete_job["shown_order"][:3]
        complete_job["variation_ids"] = list(complete_job["shown_order"])
        complete_job["blind_labels"] = {
            creative_id: chr(ord("A") + index)
            for index, creative_id in enumerate(complete_job["shown_order"])
        }
        complete_job["reaction_prompts"] = complete_job["reaction_prompts"][:3]
        self.assertEqual([], validate_job(complete_job))

        requirements = {
            ("SKILL.md", "## Non-Negotiable Runtime Contract"): (
                "`complete_exposure` is executable",
                "profile-aware capacity planner",
                "never uses maxdiff",
            ),
            ("docs/reference/methods-and-capacity.md", "## Complete exposure: two to six creatives"): (
                "both routes are executable",
                "dynamic profile-aware capacity",
            ),
            ("skills/audience-ad-testing-lab/references/input-contracts.md", "## Method Selection"): (
                "executable complete-set route",
                "profile-aware capacity plan",
            ),
            ("skills/audience-ad-testing-lab/references/scoring-rubric.md", "### `complete_exposure`"): (
                "use complete exposure for 2-6 creatives",
                "2,000 seeded whole-record bootstrap",
            ),
            ("skills/audience-ad-testing-lab/references/large-panel-orchestration.md", "## Planning And Assignment"): (
                "complete exposure is the route for 2-6 creatives",
                "dynamic profile-aware core",
            ),
            ("skills/audience-ad-testing-lab/references/examples.md", "## Complete-Exposure Example"): (
                "method-aware response validator",
                "deterministic complete-set aggregator",
            ),
            ("skills/audience-ad-testing-lab/references/synthesis-dashboard-calibration.md", "## Arbiter Boundary"): (
                "shipped method-aware response validator",
            ),
            ("skills/audience-ad-testing-lab/agents/arbiter-prompt.md", "## Non-Negotiable Rules"): (
                "validated complete-set output",
            ),
        }
        for (relative_path, heading), required in requirements.items():
            with self.subTest(relative_path=relative_path, heading=heading):
                self.assertEqual(
                    [], missing_tokens(markdown_section(relative_path, heading), required)
                )

    def test_capacity_contract_separates_unique_slots_from_model_calls_per_file(self):
        requirements = {
            ("SKILL.md", "## Non-Negotiable Runtime Contract"): (
                "unique synthetic-replicate/job slots",
                "not a provider/model-call ceiling",
                "retries and rejected attempts increase `total_model_calls`",
                "do not consume another unique job slot",
            ),
            ("docs/reference/methods-and-capacity.md", "## What the ceiling means"): (
                "unique synthetic-replicate/job slots",
                "not a provider/model-call ceiling",
                "do not consume another unique job slot",
            ),
            ("skills/audience-ad-testing-lab/references/input-contracts.md", "## Study Request"): (
                "unique synthetic-replicate/job slots",
                "not a provider/model-call ceiling",
                "retries and rejected attempts increase `total_model_calls`",
            ),
            ("skills/audience-ad-testing-lab/references/panel-contract.md", "## Units"): (
                "one unique synthetic-replicate/job slot",
                "may require multiple provider/model calls",
            ),
            ("skills/audience-ad-testing-lab/references/review-contracts.md", "## Attempt Lineage And Slot Accounting"): (
                "do not consume another unique synthetic-replicate/job slot",
                "increase `total_model_calls`",
            ),
            ("skills/audience-ad-testing-lab/references/scoring-rubric.md", "## Binding Reserves"): (
                "reserves are unique synthetic-replicate/job slots",
                "a retry or rejected attempt does not consume another slot",
            ),
            ("skills/audience-ad-testing-lab/references/large-panel-orchestration.md", "## Planning And Assignment"): (
                "unique synthetic-replicate/job slots",
                "not provider/model-call attempts",
            ),
            ("skills/audience-ad-testing-lab/references/examples.md", "## Large Static-Image Study Request"): (
                "96 unique synthetic-replicate/job slots",
                "not 96 model calls",
            ),
        }
        for (relative_path, heading), required in requirements.items():
            with self.subTest(relative_path=relative_path, heading=heading):
                self.assertEqual(
                    [], missing_tokens(markdown_section(relative_path, heading), required)
                )

    def test_respondent_derived_language_is_absent_from_every_public_file(self):
        for path in public_paths():
            with self.subTest(path=path.relative_to(ROOT)):
                self.assertNotIn(
                    "respondent-derived",
                    path.read_text(encoding="utf-8").lower(),
                )

    def test_metadata_descriptors_are_json_and_make_no_independence_claim(self):
        for relative_path in METADATA_FILES:
            with self.subTest(relative_path=relative_path):
                payload = json.loads(source(relative_path))
                serialized = json.dumps(payload, sort_keys=True).lower()
                self.assertIn("model-conditional", serialized)
                for prohibited in (
                    "independent panelist",
                    "independent synthetic",
                    "synthetic panelist",
                    "focus-group",
                ):
                    self.assertNotIn(prohibited, serialized)
                self.assertIn(ROOT / relative_path, public_paths())

        marketplace = json.loads(source(".claude-plugin/marketplace.json"))
        plugin = marketplace["plugins"][0]
        self.assertEqual(
            [
                "./skills/audience-data-lab",
                "./skills/audience-ad-testing-lab",
                "./skills/audience-panel-builder",
                "./skills/real-world-outcome-data-prep",
            ],
            plugin["skills"],
        )
        self.assertTrue(plugin["strict"])

        codex_marketplace = json.loads(
            source(".agents/plugins/marketplace.json")
        )
        self.assertEqual("innovaition-ad-testing", codex_marketplace["name"])
        codex_plugin = codex_marketplace["plugins"][0]
        self.assertEqual("audience-ad-testing-lab", codex_plugin["name"])
        self.assertEqual(
            {"source": "local", "path": "./"},
            codex_plugin["source"],
        )
        self.assertEqual(
            {
                "installation": "AVAILABLE",
                "authentication": "ON_INSTALL",
            },
            codex_plugin["policy"],
        )

        for relative_path, expected_version in (
            (".claude-plugin/plugin.json", "1.0.0"),
            (".codex-plugin/plugin.json", "1.0.0"),
            ("gemini-extension.json", "0.3.1"),
        ):
            self.assertEqual(expected_version, json.loads(source(relative_path))["version"])

        codex_manifest = json.loads(source(".codex-plugin/plugin.json"))
        self.assertLessEqual(len(codex_manifest["interface"]["defaultPrompt"]), 3)

    def test_lineage_files_and_bound_delivery_are_explicit_per_file(self):
        requirements = {
            ("SKILL.md", "## Non-Negotiable Runtime Contract"): (
                "raw-provider-returns.jsonl",
                "rejected-attempts.jsonl",
                "bind canonical paths",
                "dashboard downloads",
            ),
            ("docs/reference/outputs-and-files.md", "### Attempt lineage delivery"): (
                "raw-provider-returns.jsonl",
                "rejected-attempts.jsonl",
                "binds the canonical paths",
                "dashboard downloads",
            ),
            ("skills/audience-ad-testing-lab/references/input-contracts.md", "## Runtime And Model Lock"): (
                "raw-provider-returns.jsonl",
                "rejected-attempts.jsonl",
                "scripts/materialize-run-lineage.py",
                "sha-256 hashes",
            ),
            ("skills/audience-ad-testing-lab/references/review-contracts.md", "## Attempt Lineage And Slot Accounting"): (
                "raw-provider-returns.jsonl",
                "rejected-attempts.jsonl",
                "accepted and rejected attempt lineage",
                "accepted source provenance",
            ),
            ("skills/audience-ad-testing-lab/references/large-panel-orchestration.md", "## Claude Dynamic Workflow"): (
                "raw-provider-returns.jsonl",
                "rejected-attempts.jsonl",
                "scripts/materialize-run-lineage.py",
                "dashboard downloads",
            ),
            (
                "references/synthesis-dashboard-calibration.md",
                "### Attempt-Lineage Delivery",
            ): (
                "raw-provider-returns.jsonl",
                "rejected-attempts.jsonl",
                "sha-256 hashes",
                "downloads",
            ),
            ("skills/audience-ad-testing-lab/references/examples.md", "## Screening Response Shape"): (
                "raw-provider-returns.jsonl",
                "rejected-attempts.jsonl",
                "scripts/materialize-run-lineage.py",
                "dashboard downloads",
            ),
        }
        for (relative_path, heading), required in requirements.items():
            with self.subTest(relative_path=relative_path, heading=heading):
                self.assertEqual(
                    [], missing_tokens(markdown_section(relative_path, heading), required)
                )

    def test_heatmap_and_dashboard_contracts_are_present_in_each_owner(self):
        heatmap_requirements = {
            "SKILL.md": (
                "every inspectable media representation",
                "`copy_only` is the sole normal omission route",
                "hard stop before dashboard rendering",
            ),
            "docs/guides/test-ads.md": (
                "every inspectable media representation",
                "`copy_only` is the sole normal omission route",
                "hard stop before dashboard rendering",
            ),
            "skills/audience-ad-testing-lab/references/input-contracts.md": (
                "every inspectable media representation",
                "`copy_only` is the sole normal omission route",
                "hard stop before dashboard rendering",
            ),
            "skills/audience-ad-testing-lab/references/visual-attention-saliency.md": (
                "every inspectable media representation",
                "hard stop before dashboard rendering",
                "approved_at < revealed_at",
            ),
            "skills/audience-ad-testing-lab/references/synthesis-dashboard-calibration.md": (
                "every inspectable representation",
                "hard stop before dashboard rendering",
                "no imagery was tested",
            ),
        }
        for relative_path, required in heatmap_requirements.items():
            with self.subTest(contract="heatmap", relative_path=relative_path):
                self.assertEqual([], missing_tokens(source(relative_path), required))

        navigation_by_path = (
            (
                "SKILL.md",
                "### 10. Render, Validate, And Deliver",
                "Overview, Ads tested, Test audience, All ad results, Top ads, Feedback, Attention heatmap (imagery only), AI audience responses, Methodology, Downloads",
            ),
            (
                "docs/reference/outputs-and-files.md",
                "### Dashboard navigation",
                "Overview, Ads tested, Test audience, All ad results, Top ads, Feedback, Attention heatmap (imagery only), AI audience responses, Methodology, Downloads",
            ),
            (
                "skills/audience-ad-testing-lab/references/synthesis-dashboard-calibration.md",
                "## Marketer-First Dashboard Contract",
                "Overview, Ads tested, Test audience, All ad results, Top ads, Feedback, Attention heatmap (imagery only), AI audience responses, Methodology, Downloads",
            ),
        )
        for relative_path, heading, navigation in navigation_by_path:
            with self.subTest(contract="dashboard", relative_path=relative_path, heading=heading):
                text = markdown_section(relative_path, heading)
                self.assertIn(navigation, text)
                self.assertIn("Methodology/Test details", text)

        production_docs = "\n".join(
            source(path)
            for path in (
                "README.md",
                "SKILL.md",
                "skills/audience-ad-testing-lab/references/examples.md",
                "skills/audience-ad-testing-lab/references/synthesis-dashboard-calibration.md",
            )
        )
        self.assertNotIn("First-round results", production_docs)
        self.assertNotIn("Finalists, Feedback", production_docs)
        self.assertNotIn("opacity control", production_docs.lower())

    def test_canonical_examples_pass_shipped_validators_and_mutations_fail(self):
        request = fenced_json(
            "skills/audience-ad-testing-lab/references/examples.md", "## Large Static-Image Study Request"
        )
        parsed = StudyRequest.from_mapping(request)
        self.assertEqual(96, parsed.maximum_synthetic_panelists)

        response = fenced_json(
            "skills/audience-ad-testing-lab/references/examples.md", "## Screening Response Shape"
        )
        self.assertEqual([], validate_response(response))

        malformed_request = copy.deepcopy(request)
        malformed_request["maximum_synthetic_panelists"] = "96"
        with self.assertRaisesRegex(ValueError, "must be an integer"):
            StudyRequest.from_mapping(malformed_request)

        malformed_response = copy.deepcopy(response)
        malformed_response["assigned_variation_ids"] = malformed_response[
            "assigned_variation_ids"
        ][:3]
        self.assertIn(
            "screening_response must contain exactly four assigned variations",
            validate_response(malformed_response),
        )

    def test_adversarial_public_contract_mutations_are_detected(self):
        cases = (
            ("SKILL.md", "`complete_exposure` is executable"),
            ("skills/audience-ad-testing-lab/references/input-contracts.md", "not a provider/model-call ceiling"),
            ("skills/audience-ad-testing-lab/references/visual-attention-saliency.md", "hard stop before dashboard rendering"),
            ("skills/audience-ad-testing-lab/references/synthesis-dashboard-calibration.md", "sha-256 hashes"),
        )
        for relative_path, required in cases:
            with self.subTest(relative_path=relative_path, required=required):
                original = source(relative_path)
                self.assertEqual([], missing_tokens(original, (required,)))
                mutated = re.sub(re.escape(required), "", original, flags=re.I)
                self.assertEqual([required], missing_tokens(mutated, (required,)))

    def test_arbiter_boundary_uses_only_canonical_statuses(self):
        arbiter = source("skills/audience-ad-testing-lab/agents/arbiter-prompt.md")
        self.assertNotIn("not_used", arbiter)
        template = fenced_json("skills/audience-ad-testing-lab/agents/arbiter-prompt.md", "## Output Contract")
        boundary = template["boundary"]
        self.assertIsInstance(boundary, dict)
        self.assertEqual("resolved | unresolved | invalid", boundary["status"])
        self.assertIn("omit `boundary` or set it to `null`", arbiter)

    def test_validity_precedence_thresholds_and_reserves_are_explicit(self):
        text = runtime_instruction_text().lower()
        for statement in (
            "incomplete takes precedence while collection is open",
            "disconnected or unidentified models are invalid",
            "exploratory",
            "conditional within-run stability",
            "no population inference",
            "boundary_reserved = boundary_jobs_per_wave * boundary_waves_max",
            "finalist_reserved",
            "0.90",
            "0.10",
            "2,000",
            "0.95",
            "version-bound planned and usable floors",
            "per-profile floors",
        ):
            self.assertIn(statement, text)

    def test_recovery_config_is_strict_and_matches_runtime_defaults(self):
        config_path = ROOT / "skills/audience-ad-testing-lab/references/screening-recovery-config.json"
        config = json.loads(config_path.read_text(encoding="utf-8"))
        self.assertEqual(RECOVERY_CONFIG_KEYS, set(config))
        self.assertEqual("screening-recovery-v0-unvalidated", config["version"])
        self.assertEqual("exploratory_only", config["calibration_status"])
        self.assertEqual(9, config["planned_participation_floor"])
        self.assertEqual(8, config["usable_participation_floor"])

        maxdiff = dataclass_defaults(ROOT / "skills/audience-ad-testing-lab/scripts/audience_lab/maxdiff.py", "MaxDiffConfig")
        pairwise = dataclass_defaults(ROOT / "skills/audience-ad-testing-lab/scripts/audience_lab/pairwise.py", "PairwiseConfig")
        self.assertEqual(maxdiff["bootstrap_count"], config["bootstrap_count"])
        self.assertEqual(pairwise["bootstrap_count"], config["bootstrap_count"])
        self.assertEqual(maxdiff["successful_fit_floor"], config["successful_fit_floor"])
        self.assertEqual(pairwise["successful_fit_floor"], config["successful_fit_floor"])
        self.assertEqual(
            maxdiff["clear_finalist_threshold"],
            config["shortlist_thresholds"]["clear_finalist"],
        )
        self.assertEqual(
            maxdiff["clear_non_finalist_threshold"],
            config["shortlist_thresholds"]["clear_non_finalist"],
        )
        self.assertEqual(
            [{"name": "partial_exposure_library", "minimum": 7, "maximum": 100}],
            config["library_size_bands"],
        )
        self.assertEqual(
            [{"name": "deep_review_shortlist", "minimum": 3, "maximum": 6}],
            config["shortlist_size_bands"],
        )
        self.assertEqual({"minimum": 1, "maximum": 12}, config["segment_count"])
        self.assertEqual(
            {"minimum_rate": 0.0, "maximum_rate": 0.25},
            config["tie_inability_band"],
        )
        self.assertEqual(
            {"minimum_log_utility_gap": 0.0, "maximum_log_utility_gap": 4.0},
            config["utility_separation_band"],
        )

    def test_prompts_collect_and_synthesize_but_do_not_own_math(self):
        arbiter = (ROOT / "skills/audience-ad-testing-lab/agents/arbiter-prompt.md").read_text(encoding="utf-8")
        for deterministic_input in (
            "screening-model-results.json",
            "boundary-results.json",
            "finalist-results.json",
        ):
            self.assertIn(deterministic_input, arbiter)
        self.assertIn("Do not calculate utilities, shares, shortlist stability, or boundary decisions", arbiter)
        for prompt_owned_math in (
            "rank_points(",
            "weighted_variant_score",
            "Calculate ranked",
            "Aggregate weighted rubric",
        ):
            self.assertNotIn(prompt_owned_math, arbiter)

    def test_no_stale_nested_runtime_paths(self):
        self.assertNotIn("skills/audience-ad-testing-lab/", runtime_instruction_text())

    def test_public_markdown_avoids_the_reserved_structured_output_term(self):
        reserved = r"\b" + "arti" + r"facts?\b"
        self.assertIsNone(re.search(reserved, public_source_text(), re.IGNORECASE))

    def test_examples_are_explicitly_fictional(self):
        examples = (ROOT / "skills/audience-ad-testing-lab/references/examples.md").read_text(encoding="utf-8")
        self.assertIn("fictional Acme", examples)
        for client_name in ("Highwire", "Orkin", "Stoke"):
            self.assertNotIn(client_name, examples)

    def test_relative_markdown_links_and_inline_package_paths_resolve(self):
        markdown_link = re.compile(r"\[[^\]]+\]\(([^)]+)\)")
        inline_path = re.compile(
            r"`((?:agents|assets|references|scripts)/[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)*)`"
        )
        failures: list[str] = []
        for source in public_paths():
            text = source.read_text(encoding="utf-8")
            relative_source = source.relative_to(ROOT)
            package_root = (
                ROOT / relative_source.parts[0] / relative_source.parts[1]
                if (
                    len(relative_source.parts) >= 3
                    and relative_source.parts[0] == "skills"
                )
                else SKILL_ROOT
            )
            for target in markdown_link.findall(text):
                target = target.strip().split("#", 1)[0]
                if not target or target.startswith(("http://", "https://", "mailto:")):
                    continue
                if not (source.parent / target).resolve().exists():
                    failures.append(f"{source.relative_to(ROOT)} -> {target}")
            for target in inline_path.findall(text):
                if not (package_root / target).exists():
                    failures.append(f"{source.relative_to(ROOT)} -> {target}")
        self.assertEqual([], failures)


if __name__ == "__main__":
    unittest.main()
