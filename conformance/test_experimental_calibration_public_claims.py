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
            "### 7b. Run The Experimental Persona Behavior Calibration Sandbox",
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

    def test_public_surfaces_reject_real_world_and_internal_architecture_claims(self):
        for label, text in self.surfaces.items():
            positive_text = _positive_claim_text(text)
            for pattern in FORBIDDEN:
                with self.subTest(surface=label, pattern=pattern.pattern):
                    self.assertIsNone(pattern.search(positive_text))


if __name__ == "__main__":
    unittest.main()
