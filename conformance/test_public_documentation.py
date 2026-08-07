from __future__ import annotations

import re
import unittest
from pathlib import Path
from urllib.parse import unquote


ROOT = Path(__file__).resolve().parents[1]
SKILL_NAMES = (
    "audience-data-lab",
    "audience-panel-builder",
    "audience-ad-testing-lab",
    "real-world-outcome-data-prep",
)
LINK_RE = re.compile(r"!?\[[^\]]*\]\(([^)]+)\)")
HEADING_RE = re.compile(r"^#{1,6}\s+(.+?)\s*$", re.MULTILINE)


def public_markdown_files() -> list[Path]:
    files = [ROOT / "README.md", ROOT / "CONTRIBUTING.md"]
    files.extend((ROOT / "docs").rglob("*.md"))
    files.extend(ROOT / "skills" / name / "README.md" for name in SKILL_NAMES)
    return sorted(set(files))


def github_slug(value: str) -> str:
    value = re.sub(r"[`*_~]", "", value.strip().lower())
    value = re.sub(r"[^\w\- ]", "", value)
    return re.sub(r"\s+", "-", value)


class PublicDocumentationTests(unittest.TestCase):
    def test_private_implementation_history_is_not_public(self) -> None:
        private_label = "super" + "powers"
        self.assertFalse((ROOT / "docs" / private_label).exists())
        for markdown in public_markdown_files():
            self.assertNotIn(
                private_label,
                markdown.read_text(encoding="utf-8").lower(),
                markdown.relative_to(ROOT).as_posix(),
            )

    def test_public_documentation_files_exist(self) -> None:
        expected = {
            "docs/README.md",
            "docs/how-it-works.md",
            "docs/guides/build-an-audience-panel.md",
            "docs/guides/build-an-audience-without-research.md",
            "docs/guides/marketer-guide.md",
            "docs/guides/test-ads.md",
            "docs/guides/use-private-audience-data.md",
            "docs/guides/validate-with-real-results.md",
            "docs/concepts/research-and-grounding.md",
            "docs/concepts/profiles-replicates-and-people.md",
            "docs/concepts/synthetic-evidence-and-validity.md",
            "docs/concepts/calibration-and-real-world-validation.md",
            "docs/reference/outputs-and-files.md",
            "docs/reference/methods-and-capacity.md",
            "docs/reference/privacy-and-data-boundaries.md",
            "docs/examples/README.md",
            "docs/maintainers/development-and-release.md",
        }
        missing = sorted(path for path in expected if not (ROOT / path).is_file())
        self.assertEqual([], missing)

    def test_all_relative_links_and_images_resolve(self) -> None:
        failures: list[str] = []
        for markdown in public_markdown_files():
            text = markdown.read_text(encoding="utf-8")
            for raw_target in LINK_RE.findall(text):
                target = raw_target.strip().split(maxsplit=1)[0]
                if target.startswith(("http://", "https://", "mailto:")):
                    continue
                if target.startswith("#"):
                    destination = markdown
                    anchor = target[1:]
                else:
                    path_part, _, anchor = target.partition("#")
                    destination = (markdown.parent / unquote(path_part)).resolve()
                if not destination.exists():
                    failures.append(
                        f"{markdown.relative_to(ROOT)} -> {target} (missing target)"
                    )
                    continue
                if anchor and destination.is_file() and destination.suffix == ".md":
                    headings = {
                        github_slug(heading)
                        for heading in HEADING_RE.findall(
                            destination.read_text(encoding="utf-8")
                        )
                    }
                    if unquote(anchor).lower() not in headings:
                        failures.append(
                            f"{markdown.relative_to(ROOT)} -> {target} (missing anchor)"
                        )
        self.assertEqual([], failures)

    def test_root_readme_routes_to_all_capabilities_and_documentation(self) -> None:
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        for name in SKILL_NAMES:
            self.assertIn(f"skills/{name}/README.md", readme)
        self.assertIn("docs/README.md", readme)
        self.assertIn("docs/how-it-works.md", readme)
        self.assertIn("docs/guides/marketer-guide.md", readme)

    def test_marketer_guide_explains_research_sources_and_heatmaps(self) -> None:
        guide = (ROOT / "docs/guides/marketer-guide.md").read_text(
            encoding="utf-8"
        ).lower()
        required = (
            "u.s. census",
            "bureau of labor statistics",
            "published surveys and professional research",
            "public communities, forums, product reviews",
            "approved first-party research",
            "how research becomes a panel",
            "sum research model",
            "external image-analysis system",
            "warmer areas mean more predicted visual attention",
            "the heatmap cannot change the ai ranking or shortlist",
            "it is not eye tracking",
        )
        for marker in required:
            self.assertIn(marker, guide)

    def test_marketer_routes_explain_creation_before_reuse(self) -> None:
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        guide = (ROOT / "docs/guides/marketer-guide.md").read_text(
            encoding="utf-8"
        )
        self.assertLess(
            readme.index("Research and save a new panel"),
            readme.index("Reuse a panel you created earlier"),
        )
        self.assertLess(
            guide.index("### Research and create a reusable panel"),
            guide.index("### Reuse a saved panel"),
        )

    def test_each_skill_readme_links_to_guide_and_skill_instructions(self) -> None:
        guide_markers = {
            "audience-data-lab": "use-private-audience-data.md",
            "audience-panel-builder": "build-an-audience-panel.md",
            "audience-ad-testing-lab": "test-ads.md",
            "real-world-outcome-data-prep": "validate-with-real-results.md",
        }
        for name, guide in guide_markers.items():
            readme = (ROOT / "skills" / name / "README.md").read_text(
                encoding="utf-8"
            )
            self.assertIn(guide, readme)
            self.assertIn("(SKILL.md)", readme)

    def test_current_behavior_boundaries_are_discoverable_from_root(self) -> None:
        readme = (ROOT / "README.md").read_text(encoding="utf-8").lower()
        required = (
            "helps you test ads with synthetic audience panels",
            "provisional audience",
            "exact creative roster",
            "grounded profiles",
            "multiple ai responses",
            "human respondents",
            "synthetic-evidence-and-validity.md",
        )
        for marker in required:
            self.assertIn(marker, readme)

    def test_root_distinguishes_required_flow_from_optional_evidence_steps(self) -> None:
        readme = (ROOT / "README.md").read_text(encoding="utf-8").lower()
        required = (
            "every ad test has two required steps",
            "every test needs an audience panel",
            "always runs the creative test",
            "test 2–100 finished ads",
            "optional evidence steps",
        )
        for marker in required:
            self.assertIn(marker, readme)
        self.assertNotIn("use only the parts your project needs", readme)

    def test_root_explains_real_world_outcome_data_prep_in_plain_language(self) -> None:
        readme = (ROOT / "README.md").read_text(encoding="utf-8").lower()
        required = (
            "records the panel's prediction and campaign plan before launch",
            "imports the platform's aggregate results",
            "whether the panel's ranking matched what happened",
            "data prep does not run that comparison itself",
        )
        for marker in required:
            self.assertIn(marker, readme)

    def test_v2_capacity_is_not_presented_as_current_v3_behavior(self) -> None:
        readme = (ROOT / "README.md").read_text(encoding="utf-8").lower()
        self.assertNotIn("nine per segment", readme)
        self.assertNotIn("exactly nine", readme)

        methods = (ROOT / "docs/reference/methods-and-capacity.md").read_text(
            encoding="utf-8"
        ).lower()
        self.assertIn("current v3 runs", methods)
        self.assertIn("frozen v2 runs", methods)
        self.assertIn("does not define capacity for new v3 runs", methods)

    def test_public_documentation_has_no_maintainer_absolute_paths(self) -> None:
        failures = []
        macos_home = "/" + "Users" + "/"
        hidden_home = "~" + "/."
        for markdown in public_markdown_files():
            text = markdown.read_text(encoding="utf-8")
            if macos_home in text or hidden_home in text:
                failures.append(str(markdown.relative_to(ROOT)))
        self.assertEqual([], failures)


if __name__ == "__main__":
    unittest.main()
