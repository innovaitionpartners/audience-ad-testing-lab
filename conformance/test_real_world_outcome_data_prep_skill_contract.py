"""Public contract checks for the Real-World Outcome Data Prep skill."""

from __future__ import annotations

import json
from pathlib import Path
import re
import unittest


ROOT = Path(__file__).resolve().parents[1]
SKILL_ROOT = ROOT / "skills" / "real-world-outcome-data-prep"

EXACT_FRONTMATTER = """---
name: real-world-outcome-data-prep
version: 1.0.0
description: Prepare a real advertising outcome study before launch or import uploaded aggregate Meta, Google Ads, LinkedIn, TikTok, DV360, The Trade Desk, Amazon DSP, Xandr, or generic programmatic result files after the campaign. Use when the user wants to preregister an Ad Testing Lab prediction, avoid manually filling the real-outcome template, normalize platform exports, preserve source provenance, or create a handoff for Real-World Outcome Validation. Do not use for CRM, analytics, person-level data, evaluating whether the panel was right, changing personas, or activating panel updates.
---"""


def read(relative_path: str) -> str:
    return (ROOT / relative_path).read_text(encoding="utf-8")


def json_document(relative_path: str) -> dict[str, object]:
    value = json.loads(read(relative_path))
    if not isinstance(value, dict):
        raise AssertionError(f"{relative_path} must contain a JSON object")
    return value


class RealWorldOutcomeDataPrepSkillContractTests(unittest.TestCase):
    def test_skill_uses_exact_trigger_and_two_plain_language_modes(self):
        text = read("skills/real-world-outcome-data-prep/SKILL.md")
        self.assertTrue(text.startswith(EXACT_FRONTMATTER + "\n"))
        self.assertIn("## Prepare Study", text)
        self.assertIn("## Import Results", text)
        self.assertNotIn("Release C1", text)
        self.assertNotIn("Release C2", text)
        self.assertLess(len(text.splitlines()), 500)

    def test_skill_has_visible_approval_gated_output_and_evaluation_stop(self):
        text = read("skills/real-world-outcome-data-prep/SKILL.md")
        for required in (
            "visible study folder",
            "ask before writing",
            "Stop before Real-World Outcome Validation",
            "facts that cannot be derived",
        ):
            self.assertIn(required, text)
        lowered = text.lower()
        self.assertNotIn("default to the skill runtime", lowered)
        self.assertNotIn("fill the contract", lowered)

    def test_skill_generates_import_identity_and_time_without_asking(self):
        text = read("skills/real-world-outcome-data-prep/SKILL.md")
        self.assertIn("Generate a new import identity and import timestamp", text)
        self.assertIsNone(
            re.search(
                r"confirm\s+(?:a\s+)?(?:new\s+)?import identity\s+and\s+"
                r"import (?:time|timestamp)",
                text,
                re.IGNORECASE,
            )
        )

    def test_skill_keeps_the_preparation_boundary_closed(self):
        text = read("skills/real-world-outcome-data-prep/SKILL.md").lower()
        for required in (
            "uploaded aggregate advertising-platform files only",
            "crm",
            "analytics",
            "revenue",
            "retention",
            "person-level",
            "does not decide whether the panel was right",
            "does not compare or order results",
            "does not judge evidence",
            "does not calibrate personas",
            "does not materialize candidates",
            "does not activate changes",
            "does not mutate panels or libraries",
        ):
            self.assertIn(required, text)

    def test_progressive_disclosure_routes_to_references_and_all_clis(self):
        text = read("skills/real-world-outcome-data-prep/SKILL.md")
        for target in (
            "references/contracts.md",
            "references/operator-guide.md",
            "scripts/prepare-outcome-study.py",
            "scripts/import-outcome-results.py",
            "scripts/validate-outcome-study.py",
            "scripts/recover-outcome-study.py",
        ):
            self.assertIn(target, text)
            self.assertTrue((SKILL_ROOT / target).is_file())

    def test_operator_guide_documents_strict_per_source_context_and_maturity(self):
        text = read(
            "skills/real-world-outcome-data-prep/references/operator-guide.md"
        ).lower()
        for required in (
            "generated after upload",
            "exact source sha-256",
            "facts that cannot be derived",
            "one source context per uploaded file",
            "explicit mapping",
            "schema_tested",
            "incomplete",
            "historical",
            "descriptive_only",
            "amazon dsp",
            "blocked",
            "do not auto-detect a generic mapping",
        ):
            self.assertIn(required, text)

    def test_operator_guide_distinguishes_structural_from_authorized_handoff(self):
        text = read(
            "skills/real-world-outcome-data-prep/references/operator-guide.md"
        )
        normalized = " ".join(text.split())
        for required in (
            "An effectively preregistered, schema-tested import may include a "
            "structural `validation-handoff.json`",
            "not contract-ready or authorized for downstream Real-World "
            "Outcome Validation",
            "genuine export verification",
            "Descriptive-only imports have no validation handoff",
        ):
            self.assertIn(required, normalized)
        self.assertNotIn("No validation handoff is available yet", text)

    def test_contract_reference_names_every_closed_document(self):
        text = read(
            "skills/real-world-outcome-data-prep/references/contracts.md"
        )
        for version in (
            "outcome-study-setup-v1",
            "outcome-delivery-map-v1",
            "outcome-creative-manifest-v1",
            "outcome-registration-receipt-v1",
            "outcome-registration-receipt-v2",
            "outcome-source-governance-input-v1",
            "outcome-source-governance-record-v1",
            "outcome-source-manifest-v1",
            "outcome-correction-request-v1",
            "normalized-outcome-observation-v1",
            "outcome-observation-binding-v1",
            "outcome-prep-readiness-v1",
            "outcome-import-event-v1",
        ):
            self.assertIn(version, text)
        self.assertIn("Unknown keys fail", text)
        self.assertIn("Do not ask the user to fill", text)

    def test_marketplace_discovers_four_skills_and_manifests_are_versioned(self):
        marketplace = json_document(".claude-plugin/marketplace.json")
        plugins = marketplace["plugins"]
        self.assertIsInstance(plugins, list)
        plugin = plugins[0]
        self.assertIn("./skills/real-world-outcome-data-prep", plugin["skills"])
        self.assertEqual(4, len(plugin["skills"]))
        for path in (
            ".claude-plugin/plugin.json",
            ".codex-plugin/plugin.json",
            "gemini-extension.json",
        ):
            self.assertEqual("0.3.1", json_document(path)["version"])

    def test_panel_builder_routes_raw_real_exports_to_data_prep(self):
        text = read("skills/audience-panel-builder/SKILL.md")
        self.assertIn("Real-World Outcome Data Prep", text)
        self.assertIn("Do not normalize raw real campaign exports here", text)

    def test_readme_requires_one_verified_portable_runtime(self):
        text = read("docs/maintainers/development-and-release.md")
        self.assertIn("four connected capabilities", text)
        self.assertIn("separate skills", text)
        self.assertIn("plugin manager's installed copy", text)
        self.assertIn("stale, modified, or incomplete release bytes", text)
        self.assertIn("Real-World Outcome Validation", text)
        self.assertNotIn("/" + "Users" + "/", text)
        self.assertNotIn("12_Skills/audience-ad-testing-lab", text)

    def test_readme_leads_with_the_real_study_workflow(self):
        text = read("docs/guides/validate-with-real-results.md")
        real_steps = (
            "Record the panel's prediction and campaign plan before launch",
            "Run the campaign",
            "import the original results file exported from the ad platform",
            "start Real-World Outcome Validation",
        )
        positions = [text.index(step) for step in real_steps]
        self.assertEqual(sorted(positions), positions)
        self.assertIn("Validation never edits the panel automatically", text)
        self.assertNotIn("fictional study", text.lower())
        self.assertNotIn("synthetic study", text.lower())
        self.assertNotIn("Release C1", text)
        self.assertNotIn("Release C2", text)
        self.assertIsNone(re.search(r"\bC[12]\b", text))

    def test_readme_labels_sandbox_as_internal_known_answer_harness(self):
        text = read("docs/concepts/calibration-and-real-world-validation.md")
        self.assertIn("internal known-answer engineering harness", text)
        for prohibited_claim in (
            "cannot establish real-world operability",
            "cannot establish panel validity",
            "cannot establish market behavior",
            "cannot establish production calibration",
        ):
            self.assertIn(prohibited_claim, text)


if __name__ == "__main__":
    unittest.main()
