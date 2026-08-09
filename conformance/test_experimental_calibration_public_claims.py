"""Public-language guardrails for the experimental calibration sandbox."""

from __future__ import annotations

import re
from pathlib import Path
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "skills" / "audience-panel-builder" / "scripts"
sys.path.insert(0, str(SCRIPTS))

from conformance.experimental_calibration_fixtures import (  # noqa: E402
    evaluation_inputs_fixture,
)
from experimental_persona_calibration_oracle.evaluator import (  # noqa: E402
    evaluate_synthetic_study,
)
from experimental_persona_calibration_oracle.reporting import (  # noqa: E402
    render_experimental_report,
)


TITLE = "Experimental Persona Behavior Calibration Sandbox"
INTRO = (
    "This sandbox uses fictional synthetic fixtures to propose and materialize "
    "a draft update to one existing persona. It does not validate real-world "
    "accuracy, cannot create a reusable package, and cannot register or "
    "activate a panel."
)
DISCLAIMER = (
    "Built and evaluated with fictional synthetic fixtures only. This output "
    "does not validate real-world panel accuracy, does not prove that the "
    "proposed change will improve outcomes, and cannot modify an active panel."
)
FORBIDDEN = (
    re.compile(r"\bvalidated? (?:against|with) real[- ]world", re.I),
    re.compile(r"\bcalibrated? (?:against|with) real[- ]world", re.I),
    re.compile(r"\bpredicts? (?:campaign|ad|panel|real[- ]world) performance", re.I),
    re.compile(r"(?<!not )\bproves? (?:a )?(?:persona )?preference", re.I),
    re.compile(r"\bdirect preference measurement", re.I),
    re.compile(r"\b(?:C1|C2|Tier 4)\b"),
)


def _section(text: str, heading: str) -> str:
    start = text.index(heading)
    tail = text[start:]
    match = re.search(r"\n#{1,3} ", tail[len(heading):])
    return tail if match is None else tail[: len(heading) + match.start()]


def _positive_claim_text(text: str) -> str:
    plain = re.sub(r"<[^>]+>", " ", text)
    sentences = re.split(r"(?<=[.!?])\s+", plain)
    negations = (
        "cannot",
        "does not",
        "do not",
        "not proven",
        "is not",
        "never",
    )
    return " ".join(
        sentence
        for sentence in sentences
        if not any(token in sentence.casefold() for token in negations)
    )


class ExperimentalCalibrationPublicClaimsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        reference = (
            ROOT
            / "skills"
            / "audience-panel-builder"
            / "references"
            / "experimental-persona-behavior-calibration.md"
        ).read_text()
        public_concept = _section(
            (ROOT / "docs" / "concepts" / "calibration-and-real-world-validation.md").read_text(),
            "## Experimental Persona Behavior Calibration Sandbox",
        )
        skill = _section(
            (
                ROOT
                / "skills"
                / "audience-panel-builder"
                / "SKILL.md"
            ).read_text(),
            "### 7c. Run The Experimental Persona Behavior Calibration Sandbox",
        )
        inputs = evaluation_inputs_fixture()
        evaluation = evaluate_synthetic_study(**inputs)
        template = (
            ROOT
            / "skills"
            / "audience-panel-builder"
            / "assets"
            / "experimental-persona-behavior-report-template.html"
        ).read_text()
        report = render_experimental_report(
            evaluation=evaluation,
            proposals=inputs["proposals"],
            candidates=inputs["candidates"],
            template=template,
        )
        cls.surfaces = {
            "reference": reference,
            "public calibration concept": public_concept,
            "SKILL sandbox section": skill,
            "rendered sandbox report": report,
        }
        cls.real_world_surfaces = {
            "root README": (ROOT / "README.md").read_text(),
            "Panel Builder README": (
                ROOT / "skills" / "audience-panel-builder" / "README.md"
            ).read_text(),
            "panel-building guide": (
                ROOT / "docs" / "guides" / "build-an-audience-panel.md"
            ).read_text(),
            "real-results guide": (
                ROOT / "docs" / "guides" / "validate-with-real-results.md"
            ).read_text(),
            "calibration concept": (
                ROOT
                / "docs"
                / "concepts"
                / "calibration-and-real-world-validation.md"
            ).read_text(),
            "real-world technical reference": (
                ROOT
                / "skills"
                / "audience-panel-builder"
                / "references"
                / "real-world-persona-behavior-calibration.md"
            ).read_text(),
        }

    def test_public_surfaces_require_exact_fictional_nonregisterable_boundary(self):
        for label, text in self.surfaces.items():
            with self.subTest(surface=label):
                self.assertIn(TITLE, text)
                self.assertRegex(
                    text,
                    r"(?i)cannot (?:be )?register(?:ed)? or activate(?:d)?",
                )
        for label in ("reference", "SKILL sandbox section"):
            with self.subTest(surface=label):
                self.assertIn(INTRO, self.surfaces[label])
                self.assertIn(DISCLAIMER, self.surfaces[label])
        public_concept = self.surfaces["public calibration concept"]
        self.assertIn("internal known-answer engineering harness", public_concept)
        for required in (
            "cannot establish real-world operability",
            "cannot establish panel validity",
            "cannot establish market behavior",
            "cannot establish production calibration",
        ):
            self.assertIn(required, public_concept)
        report = self.surfaces["rendered sandbox report"]
        for required in (
            "Built and evaluated with fictional synthetic fixtures only.",
            "This output does not validate real-world panel accuracy.",
            "The proposal is not proven to improve real-world outcomes.",
            "This report cannot modify an active panel.",
        ):
            self.assertIn(required, report)

    def test_public_surfaces_preserve_sandbox_and_explain_real_world_user_boundary(self):
        for label, text in self.surfaces.items():
            positive_text = _positive_claim_text(text)
            for pattern in FORBIDDEN:
                with self.subTest(surface=label, pattern=pattern.pattern):
                    self.assertIsNone(pattern.search(positive_text))

        required_by_surface = {
            "root README": (
                "Improve a panel from real campaign results",
                "You provide or identify the aggregate campaign-result exports.",
                "The workflow pauses until you run a fresh held-out campaign",
            ),
            "Panel Builder README": (
                "Improve an existing panel from real results",
                "The system handles the rest of the evidence workflow",
                "The user does not assemble validation packages",
            ),
            "panel-building guide": (
                "ask Audience Panel Builder to improve the panel from real results",
                "You do not prepare the evidence packages or edit the panel yourself.",
            ),
            "real-results guide": (
                "### What you do",
                "### What happens automatically",
                "provide the result exports, not hand-built calibration files",
            ),
            "calibration concept": (
                "guided two-phase improvement workflow",
                "The user does not assemble the internal evidence graph",
            ),
            "real-world technical reference": (
                "## User-facing boundary",
                "The skill owns authentication",
                "they are not customer instructions",
            ),
        }
        for label, required in required_by_surface.items():
            text = self.real_world_surfaces[label]
            for phrase in required:
                with self.subTest(surface=label, phrase=phrase):
                    self.assertIn(phrase, text)

        customer_docs = "\n".join(
            self.real_world_surfaces[label]
            for label in (
                "root README",
                "Panel Builder README",
                "panel-building guide",
                "real-results guide",
                "calibration concept",
            )
        )
        for internal_input in (
            "--base-panel-package",
            "--authority-secret-file",
            "registration_proposal_sha256",
        ):
            self.assertNotIn(internal_input, customer_docs)


if __name__ == "__main__":
    unittest.main()
