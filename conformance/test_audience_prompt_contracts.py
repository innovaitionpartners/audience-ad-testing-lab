"""Contract tests for the research and panel-builder worker instructions."""

from __future__ import annotations

from pathlib import Path
import json
import re
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]
SKILL = ROOT / "skills" / "audience-ad-testing-lab"
sys.path.insert(0, str(SKILL / "scripts"))

from audience_lab import audience_research as schemas  # noqa: E402


def read(relative: str) -> str:
    return (SKILL / relative).read_text(encoding="utf-8")


class AudiencePromptContractTests(unittest.TestCase):
    def test_prompt_schema_terms_cover_validator_allowlists_and_fixture_pair_passes(self) -> None:
        researcher = read("agents/audience-researcher-prompt.md")
        builder = read("agents/audience-panel-builder-prompt.md")
        for key_set in (
            schemas._BRIEF_KEYS,
            schemas._TARGET_KEYS,
            schemas._PRIVACY_KEYS,
            schemas._APPROVAL_KEYS,
            schemas._EVIDENCE_KEYS,
            schemas._FINDING_KEYS,
            schemas._COVERAGE_KEYS,
            schemas._HYPOTHESIS_KEYS,
            schemas._GAP_KEYS,
        ):
            for key in key_set:
                self.assertIn(key, researcher)
        for key_set in (
            schemas._PANEL_KEYS,
            schemas._SCOPE_KEYS,
            schemas._PERSONA_RESEARCH_KEYS,
            schemas._SEGMENT_KEYS,
            schemas._ARCHETYPE_KEYS,
            schemas._STRATUM_KEYS,
            schemas._DIMENSION_KEYS,
            schemas._PROFILE_KEYS,
            schemas._SNAPSHOT_KEYS,
            schemas._PROVENANCE_KEYS,
            schemas._REPLICATE_KEYS,
            schemas._CALIBRATION_KEYS,
            schemas._REFRESH_KEYS,
            schemas._GOVERNANCE_KEYS,
        ):
            for key in key_set:
                self.assertIn(key, builder)
        fixtures = ROOT / "conformance" / "fixtures" / "audience-research"
        brief = json.loads((fixtures / "approved-brief.json").read_text())
        panel = json.loads((fixtures / "approved-panel.json").read_text())
        self.assertEqual([], schemas.validate_research_brief(brief))
        self.assertEqual([], schemas.validate_saved_panel(panel, brief))
        self.assertEqual([], schemas.validate_audience_research_pair(brief, panel))

    def test_researcher_owns_research_brief_but_not_panel_construction(self) -> None:
        text = read("agents/audience-researcher-prompt.md")
        for token in (
            "audience-research-brief-v2",
            "research_derived",
            "user_proposed_research_validated",
            "provisional_user_defined",
            "evidence_sources",
            "findings",
            "coverage",
            "segment_hypotheses",
            "evidence_gaps",
            "privacy_confirmation",
            "approved_for_panel_creation: true",
            "strong | thin | empty",
        ):
            self.assertIn(token, text)
        self.assertIn("Do not create persona archetypes", text)
        self.assertIn("Do not create a saved audience panel", text)
        self.assertIn("Never invent URLs", text)
        self.assertIn("aggregated and anonymized", text)
        self.assertIn("approval_gate", text)
        self.assertIn("approved_emit", text)
        self.assertIn("user-supplied approval record", text.lower())

    def test_researcher_subagents_are_bounded_and_codex_only(self) -> None:
        text = read("agents/audience-researcher-prompt.md")
        self.assertIn("Codex-Only Bounded Research Option", text)
        self.assertRegex(text, r"at most \d+ research subagents")
        self.assertIn("Do not delegate approval", text)
        self.assertIn("Claude Code", text)
        self.assertIn("without subagents", text)

    def test_approval_gate_freezes_complete_candidate_with_narrow_mutations(self) -> None:
        text = read("agents/audience-researcher-prompt.md")
        self.assertIn("complete draft-shaped", text)
        self.assertIn("field-for-field", text)
        self.assertIn("store those exact candidate bytes", text)
        allowlist_match = re.search(
            r"explicit mutation allowlist is exactly:\n\n```text\n([^`]+)```",
            text,
        )
        self.assertIsNotNone(allowlist_match)
        allowed = {
            token.strip()
            for token in allowlist_match.group(1).split(",")
            if token.strip()
        }
        self.assertEqual({"status", "updated_at", "approval"}, allowed)
        frozen_match = re.search(
            r"complete frozen field set is exactly:\n\n```text\n([^`]+)```",
            text,
        )
        self.assertIsNotNone(frozen_match)
        frozen = {
            token.strip()
            for token in frozen_match.group(1).replace("\n", " ").split(",")
            if token.strip()
        }
        self.assertEqual(
            schemas._BRIEF_KEYS - {"status", "updated_at", "approval"},
            frozen,
        )
        self.assertIn("Canonical-JSON compare every frozen field", text)
        self.assertIn("must return to `approval_gate`", text)

    def test_builder_requires_approved_brief_and_exact_validated_schemas(self) -> None:
        text = read("agents/audience-panel-builder-prompt.md")
        for token in (
            "saved-audience-panel-v2",
            "approved_for_panel_creation: true",
            "validate_audience_research_pair",
            "grounded_context_profiles",
            "context_attribute_provenance",
            "scope_fingerprint",
            "refresh_conditions",
            "governance",
        ):
            self.assertIn(token, text)
        self.assertIn("exact schema allowlists", text)
        self.assertIn("Do not add findings, evidence IDs, or segments", text)
        self.assertIn("Do not create an implicit archetype-by-stratum cross-product", text)
        self.assertIn("Stop and return validation errors", text)

    def test_construction_auditor_has_exact_blind_inputs_and_strict_output(self) -> None:
        text = (ROOT / "skills" / "audience-panel-builder" / "agents" / "panel-construction-auditor.md").read_text(encoding="utf-8")
        release_a_match = re.search(
            r"For Release A, receive these six approved canonical research "
            r"documents:\n\n```text\n([^`]+)```",
            text,
        )
        self.assertIsNotNone(release_a_match)
        self.assertEqual(
            "approved brief, saved panel, ledger, finding support, synthesis matrix, report manifest",
            release_a_match.group(1).strip(),
        )
        self.assertIn(
            "Also receive the exact `panel-review-manifest.json`",
            text,
        )
        release_b1_match = re.search(
            r"For Release B1, receive those six documents plus:\n\n"
            r"```text\n([^`]+)```",
            text,
        )
        self.assertIsNotNone(release_b1_match)
        self.assertEqual(
            [
                "canonical population-frame result",
                "canonical composition plan",
                "canonical panel-final validity profile",
                "canonical authorized-audience handoff, only when one is bound",
            ],
            release_b1_match.group(1).strip().splitlines(),
        )
        for phrase in (
            "creative, evaluation output, performance output, campaign\noutcomes, winner labels, or the requesting model's private reasoning",
            "Return only strict `panel-construction-audit-v1` JSON",
            "Do not\nredesign the panel, repair its evidence, or judge any creative.",
        ):
            self.assertIn(phrase, text)
        self.assertIn(
            "They are not hashes of\nthe uploaded files' incidental whitespace or key order.",
            text,
        )
        audit_keys = {
            "schema_version", "panel_id", "panel_version", "auditor_run_id", "audited_at",
            "input_bindings", "checks", "result", "limitations",
        }
        for key in audit_keys:
            self.assertIn(key, text)

    def test_live_auditor_dispatch_and_worker_contract_are_complete_and_blind(self) -> None:
        skill = (ROOT / "skills" / "audience-panel-builder" / "SKILL.md").read_text(encoding="utf-8")
        dispatch = " ".join(
            skill.split("`agents/panel-construction-auditor.md`", 1)[1]
            .split("Fix unsupported", 1)[0]
            .split()
        )
        self.assertIn(
            "only the worker prompt and these seven approved canonical documents: approved brief, saved panel, ledger, finding support, synthesis matrix, panel-review manifest, and research-report manifest",
            dispatch,
        )
        for forbidden in ("construction rules", "v2 allowlists", "candidate creative", "private reasoning"):
            self.assertIn(forbidden, dispatch)
        worker = (ROOT / "skills" / "audience-panel-builder" / "agents" / "panel-construction-auditor.md").read_text(encoding="utf-8")
        for binding in (
            "brief_sha256", "panel_sha256", "evidence_ledger_sha256", "finding_support_sha256",
            "synthesis_matrix_sha256", "report_manifest_sha256", "population_frame_sha256",
            "composition_plan_sha256", "validity_profile_sha256", "authorized_handoff_sha256",
        ):
            self.assertIn(binding, worker)
        self.assertIn("Do not use `not_applicable` to mask an applicable failure.", worker)
        for check_id in (
            "approved_evidence_only", "finding_support_complete", "contradictions_preserved",
            "segment_sufficiency", "profile_traceability", "inference_boundaries", "privacy_boundary",
            "count_semantics", "claim_tier", "weight_semantics", "population_frame_traceability",
            "authorized_handoff_traceability",
        ):
            self.assertIn(check_id, worker)

    def test_panel_builder_canonical_package_flow_stops_nonapproved_routes(self) -> None:
        skill = (ROOT / "skills" / "audience-panel-builder" / "SKILL.md").read_text(encoding="utf-8")
        workflow = (
            ROOT
            / "skills"
            / "audience-panel-builder"
            / "references"
            / "route-workflows-and-output-templates.md"
        ).read_text(encoding="utf-8")
        combined = skill + "\n" + workflow
        ordered = (
            "synthesize evidence",
            "approve `evidence_synthesis`",
            "construct panel",
            "render real report",
            "blind construction audit",
            "approve `panel_construction`",
            "calculate package proposal digest without materializing a reusable package",
            "approve `package_registration`",
            "build the canonical package",
            "register",
        )
        sequence = "\n→ ".join(ordered)
        self.assertIn(sequence, combined.lower())
        self.assertIn("Dogfood exits after report and audit", combined)
        self.assertIn(
            "Provisional work cannot call either canonical package entry point",
            combined,
        )
        for command in (
            "python3 scripts/propose-panel-package.py",
            "python3 scripts/build-approved-panel-package.py",
            "python3 scripts/register-approved-panel.py",
        ):
            command_lines = [
                line for line in combined.splitlines() if command in line
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

    def test_skill_hands_new_audiences_out_before_planning(self) -> None:
        text = read("SKILL.md")
        audience_workflow = text.split("Choose exactly one audience route", 1)[1].split(
            "### 2. Normalize", 1
        )[0]
        self.assertIn("New-audience handoff", audience_workflow)
        self.assertIn("stop before run planning", audience_workflow)
        self.assertIn("must not invoke another skill", audience_workflow)
        self.assertIn(
            "user or outer orchestration layer runs Audience Panel Builder",
            audience_workflow,
        )
        self.assertIn("Saved-panel route", text)
        self.assertIn("resolution → planning", text)
        self.assertIn("Provisional route", text)
        self.assertIn("without registration", text)

    def test_references_define_exact_routes_and_approval_boundary(self) -> None:
        inputs = read("references/input-contracts.md")
        research = read("references/persona-research.md")
        panel = read("references/panel-contract.md")
        for route in ("target_audience", "audience_panel", "provisional_audience"):
            self.assertIn(route, inputs)
        self.assertIn("exactly one", inputs)
        self.assertIn("public_research", inputs)
        self.assertIn("supplied_research_paths", inputs)
        self.assertIn("package for the immediate run without registration", inputs)
        self.assertIn("approval is a user decision", research.lower())
        self.assertIn("panel construction begins only", research.lower())
        self.assertIn("approved findings", panel.lower())
        self.assertIn("exact schema", panel.lower())
        self.assertIn("provisional_no_research", panel)

    def test_display_name_changes_without_internal_identifier_drift(self) -> None:
        skill = read("SKILL.md")
        self.assertIn("# Ad Testing Lab", skill)
        self.assertRegex(skill, r"(?m)^name: audience-ad-testing-lab$")
        self.assertNotIn("# Audience Ad Testing Lab", skill)
        combined = "\n".join(
            read(path)
            for path in (
                "agents/audience-researcher-prompt.md",
                "agents/audience-panel-builder-prompt.md",
                "references/persona-research.md",
                "references/panel-contract.md",
                "references/input-contracts.md",
            )
        )
        self.assertNotIn("Audience Ad Testing Lab", combined)
        self.assertIsNotNone(re.search(r"\bAd Testing Lab\b", combined))

    def test_release_a_claims_and_count_language_are_explicit_and_bounded(self) -> None:
        builder_root = ROOT / "skills" / "audience-panel-builder"
        texts = {
            path: (builder_root / path).read_text(encoding="utf-8")
            for path in ("SKILL.md", "references/contracts.md", "references/construction-method.md")
        }
        combined = "\n".join(texts.values())
        for phrase in (
            "Directional creative hypothesis stress test.",
            "Synthetic panel output is not a customer survey or a human sample.",
            "This synthetic panel is not a representative human sample.",
            "Tier 1 evidence-grounded panel",
            "Population composition not available",
        ):
            self.assertIn(phrase, combined)
        review_surface = (builder_root / "scripts" / "audience_panel_builder" / "review.py").read_text(encoding="utf-8").lower()
        for prohibited in (
            "is representative", "statistically representative",
            "population representativeness", "market representativeness",
            "predictive lift", "market share", "calibrated audience",
        ):
            self.assertNotIn(prohibited, review_surface)
        self.assertIn("not a representative human sample", review_surface)
        self.assertNotIn("statistical representativeness", texts["SKILL.md"].lower())


if __name__ == "__main__":
    unittest.main()
