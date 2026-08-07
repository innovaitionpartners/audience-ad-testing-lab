"""Prompt-contract checks for the run-local provisional audience route."""

from __future__ import annotations

from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


def read(relative_path: str) -> str:
    return (ROOT / relative_path).read_text(encoding="utf-8")


def compact(value: str) -> str:
    return " ".join(value.split())


class ProvisionalNoResearchRouteContractTests(unittest.TestCase):
    def test_ad_testing_lab_owns_user_facing_provisional_route(self) -> None:
        text = read("skills/audience-ad-testing-lab/SKILL.md")
        for required in (
            "When creatives are supplied, Ad Testing Lab owns the user-facing provisional route",
            "do not send the user through a separate Audience Panel Builder approval workflow",
            "internal run-local materialization helper",
            "The run plan is the only approval gate",
        ):
            self.assertIn(required, text)

    def test_provisional_route_skips_empty_research_approvals(self) -> None:
        ad_lab = compact(read("skills/audience-ad-testing-lab/SKILL.md"))
        panel_builder = compact(read(
            "skills/audience-panel-builder/references/route-workflows-and-output-templates.md"
        ))
        for text in (ad_lab, panel_builder):
            for required in (
                "no research-plan approval",
                "no research-brief approval",
                "no panel-package approval",
            ):
                self.assertIn(required, text)

    def test_provisional_unknowns_expiry_and_non_reuse_are_automatic(self) -> None:
        ad_lab = read("skills/audience-ad-testing-lab/SKILL.md")
        panel_builder = read("skills/audience-panel-builder/SKILL.md")
        route_reference = read(
            "skills/audience-panel-builder/references/route-workflows-and-output-templates.md"
        )
        combined = "\n".join((ad_lab, panel_builder, route_reference))
        for required in (
            "unsupported fields to `unknown`",
            "automatically set expiry",
            "no more than 30 days",
            "never register or reuse",
        ):
            self.assertIn(required, combined)

    def test_no_creatives_retains_scope_without_materializing_panel(self) -> None:
        text = read("skills/audience-ad-testing-lab/SKILL.md")
        for required in (
            "If no creatives are supplied",
            "retain only a draft audience scope",
            "do not materialize a provisional panel",
        ):
            self.assertIn(required, text)

    def test_minimal_provisional_scope_is_deterministic_and_does_not_invent_profiles(self) -> None:
        text = read("skills/audience-ad-testing-lab/SKILL.md")
        for required in (
            "one segment for each cohort the user explicitly distinguishes",
            "one grounded profile for each materially distinct role or context",
            "exactly one segment and one grounded profile",
            "Never invent extra profiles",
        ):
            self.assertIn(required, text)

    def test_runtime_fills_required_provisional_control_fields(self) -> None:
        text = compact(read(
            "skills/audience-ad-testing-lab/references/input-contracts.md"
        ))
        for required in (
            "runtime-populated control fields",
            "not a second user approval",
            "derives `accepted_by`",
            "sets `accepted_at`",
            "calculates `expires_at` automatically",
        ):
            self.assertIn(required, text)

    def test_marketer_counts_name_each_distinct_denominator(self) -> None:
        text = read("skills/audience-ad-testing-lab/SKILL.md")
        for required in (
            "grounded profiles",
            "planned isolated synthetic executions",
            "minimum usable feedback records",
            "accepted feedback records",
            "model calls",
            "human respondents: 0",
        ):
            self.assertIn(required, text)

    def test_panel_builder_labels_provisional_materialization_internal(self) -> None:
        skill = compact(read("skills/audience-panel-builder/SKILL.md"))
        route_reference = compact(read(
            "skills/audience-panel-builder/references/route-workflows-and-output-templates.md"
        ))
        for text in (skill, route_reference):
            self.assertIn("internal run-local helper", text)
            self.assertIn("When creatives are supplied", text)
            self.assertIn("Ad Testing Lab", text)


if __name__ == "__main__":
    unittest.main()
