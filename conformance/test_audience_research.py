from copy import deepcopy
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "skills" / "audience-ad-testing-lab" / "scripts"))

from audience_lab.audience_research import (  # noqa: E402
    AudienceResearchValidationError,
    compute_scope_fingerprint,
    require_valid_audience_research_pair,
    validate_audience_research_pair,
    validate_saved_panel,
)


class AudienceResearchContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        fixture = ROOT / "conformance" / "fixtures" / "audience-research"
        cls.brief = json.loads((fixture / "approved-brief.json").read_text())
        cls.panel = json.loads((fixture / "approved-panel.json").read_text())

    def errors(self, brief=None, panel=None):
        return validate_audience_research_pair(
            deepcopy(self.brief if brief is None else brief),
            deepcopy(self.panel if panel is None else panel),
            now=datetime(2026, 7, 22, tzinfo=timezone.utc),
        )

    def assert_code(self, code, brief=None, panel=None, path=None):
        errors = self.errors(brief, panel)
        matches = [error for error in errors if error.code == code]
        self.assertTrue(matches, [error.to_dict() for error in errors])
        if path is not None:
            self.assertIn(path, [error.path for error in matches])

    def test_valid_approved_pair(self):
        self.assertEqual([], self.errors())
        require_valid_audience_research_pair(self.brief, self.panel)

    def test_scope_fingerprint_normalizes_case_unicode_and_whitespace(self):
        scope = deepcopy(self.panel["audience_scope"])
        expected = scope.pop("scope_fingerprint")
        scope["audience"] = "  OPERATIONS   LEADERS AT MID-MARKET SOFTWARE COMPANIES "
        self.assertEqual(expected, compute_scope_fingerprint(scope))

    def test_strict_unknown_key_rejected_at_every_representative_depth(self):
        panel = deepcopy(self.panel)
        panel["persona_archetypes"][0]["helpful_extra"] = True
        self.assert_code("unknown_field", panel=panel, path="$.persona_archetypes[0].helpful_extra")

    def test_canonical_ids_and_versions_only(self):
        panel = deepcopy(self.panel)
        panel["panel_id"] = "../Operations_Leaders"
        panel["version"] = "v1"
        self.assert_code("invalid_identifier", panel=panel, path="$.panel_id")
        self.assert_code("invalid_version", panel=panel, path="$.version")

    def test_unapproved_brief_is_rejected(self):
        brief = deepcopy(self.brief)
        brief["approval"]["approved_for_panel_creation"] = False
        self.assert_code("brief_not_approved", brief=brief)

    def test_finding_and_segment_cross_file_resolution(self):
        brief = deepcopy(self.brief)
        brief["findings"][0]["evidence_ids"] = ["missing-evidence"]
        self.assert_code("unresolved_evidence", brief=brief)
        panel = deepcopy(self.panel)
        panel["segments"][0]["finding_ids"] = ["unapproved-finding"]
        self.assert_code("unresolved_finding", panel=panel)

    def test_segment_origin_must_match_approved_hypothesis(self):
        panel = deepcopy(self.panel)
        panel["segments"][0]["origin"] = "user_proposed_research_validated"
        self.assert_code("segment_origin_mismatch", panel=panel)

    def test_archetype_stratum_and_profile_must_resolve_same_segment(self):
        panel = deepcopy(self.panel)
        panel["segments"].append({**deepcopy(panel["segments"][0]), "segment_id": "second-segment", "name": "Second"})
        panel["context_strata"][0]["segment_id"] = "second-segment"
        self.assert_code("unsupported_profile_combination", panel=panel)

    def test_no_implicit_archetype_by_stratum_expansion(self):
        panel = deepcopy(self.panel)
        second = deepcopy(panel["context_strata"][0])
        second["context_stratum_id"] = "renewal-review"
        panel["context_strata"].append(second)
        self.assertEqual([], self.errors(panel=panel))
        self.assertEqual(1, len(panel["grounded_context_profiles"]))

    def test_profile_provenance_must_exactly_match_selected_stratum(self):
        panel = deepcopy(self.panel)
        panel["grounded_context_profiles"][0]["context_attribute_provenance"][0]["value"] = "unaware"
        self.assert_code("profile_provenance_mismatch", panel=panel)

    def test_archetype_traits_require_resolving_provenance(self):
        panel = deepcopy(self.panel)
        panel["persona_archetypes"][0]["finding_ids"] = []
        self.assert_code("empty_array", panel=panel, path="$.persona_archetypes[0].finding_ids")

    def test_unsubstantiated_weight_must_be_labeled_planning_allocation(self):
        panel = deepcopy(self.panel)
        panel["segments"][0]["weight_source_evidence"] = []
        self.assert_code("unsupported_weight_provenance", panel=panel)
        panel["segments"][0]["weighting_rule"] = "planning_allocation"
        self.assertNotIn("unsupported_weight_provenance", {e.code for e in self.errors(panel=panel)})

    def test_provisional_requires_no_sources_and_expiry_within_30_days(self):
        brief = deepcopy(self.brief)
        brief["status"] = "provisional_no_research"
        brief["research_mode"] = "provisional_no_research"
        brief["evidence_sources"] = []
        brief["findings"] = []
        brief["research_questions"] = []
        brief["coverage"] = {key: "empty" for key in brief["coverage"]}
        brief["segment_hypotheses"][0].update(origin="provisional_user_defined", finding_ids=[], evidence_ids=[], confidence="low")
        panel = deepcopy(self.panel)
        panel["persona_research"].update(
            mode="provisional_no_research", status="provisional_no_research",
            expires_at="2026-07-30T12:00:00Z", source_types=[], evidence_ids=[],
            source_state="no_research_sources",
        )
        panel["persona_research"]["coverage"] = {key: "empty" for key in panel["persona_research"]["coverage"]}
        panel["segments"][0].update(origin="provisional_user_defined", weighting_rule="planning_allocation", weight_source_evidence=[], finding_ids=[], evidence_ids=[])
        panel["persona_archetypes"][0].update(finding_ids=[], evidence_ids=[], evidence_strength="low")
        panel["context_strata"][0]["dimensions"][0].update(status="experimental", source_evidence=[], finding_ids=[])
        panel["grounded_context_profiles"][0]["context_attribute_provenance"][0].update(status="experimental", source_evidence=[], finding_ids=[])
        self.assertEqual([], self.errors(brief=brief, panel=panel))
        panel["persona_research"]["expires_at"] = "2026-09-15T12:00:00Z"
        self.assert_code("invalid_provisional_expiry", brief=brief, panel=panel)

    def test_synthetic_profiles_may_model_sensitive_concepts_without_person_data(self):
        brief = deepcopy(self.brief)
        brief["findings"][0]["statement"] = "Contact analyst@example.com about Black and Muslim union members and political affiliation."
        codes = {error.code for error in self.errors(brief=brief)}
        self.assertIn("pii_email", codes)
        self.assertNotIn("blocked_sensitive_trait", codes)
        for phrase in (
            "racial group", "ethnic cohort", "religious buyers", "disabled leaders",
            "health condition", "health status", "medical condition",
            "medical diagnosis", "medical history", "patient diagnosis",
            "diagnosed with a chronic condition", "political affiliation",
            "union member", "citizenship",
            "immigration status", "exact coordinates", "GPS location", "DNA profile",
            "genetic data", "biometric data", "sexual orientation", "financial account",
            "Black buyers", "Hispanic leaders", "Christian executives", "Muslim operators",
            "gay consumers", "lesbian leaders", "wheelchair user", "Democratic voter",
            "Republican voter", "AFL-CIO member", "noncitizen", "undocumented immigrant",
        ):
            panel = deepcopy(self.panel)
            panel["segments"][0]["description"] = phrase
            codes = {error.code for error in self.errors(panel=panel)}
            self.assertNotIn("blocked_sensitive_trait", codes)

    def test_health_sector_context_and_synthetic_health_cohort_are_allowed(self):
        brief = deepcopy(self.brief)
        panel = deepcopy(self.panel)
        scope_updates = {
            "audience": "Health care CMOs and CCOs",
            "category": "Health care communications",
            "market": "Health systems, digital health, and life sciences",
            "buying_context": "Selecting communications support for a health system",
        }
        brief["target_audience"].update(scope_updates)
        panel["audience_scope"].update(scope_updates)
        panel["audience_scope"]["scope_fingerprint"] = compute_scope_fingerprint(
            panel["audience_scope"]
        )
        brief["segment_hypotheses"][0]["name"] = (
            "Health care communications leaders serving people with chronic health conditions"
        )
        brief["segment_hypotheses"][0]["evidence_ids"] = ["health-sector-evidence"]
        panel["segments"][0]["description"] = (
            "CMOs and CCOs at health care organizations communicating with people "
            "managing chronic health conditions."
        )
        panel["segments"][0]["evidence_ids"] = ["health-sector-evidence"]
        panel["persona_archetypes"][0]["role_context"] = "Health system CMO or CCO"
        panel["grounded_context_profiles"][0]["profile_snapshot"]["role_context"] = (
            "Health system CMO or CCO"
        )

        codes = {error.code for error in self.errors(brief=brief, panel=panel)}
        self.assertNotIn("blocked_sensitive_trait", codes)

    def test_precise_gps_values_are_raw_individual_data_everywhere(self):
        brief = deepcopy(self.brief)
        brief["findings"][0]["statement"] = "Observed at 40.7128,-74.0060."
        self.assert_code("pii_precise_geolocation", brief=brief)

    def test_malformed_source_url_rejected(self):
        brief = deepcopy(self.brief)
        brief["evidence_sources"][0]["source_url"] = "file:///private/report.csv"
        self.assert_code("invalid_source_url", brief=brief)

    def test_phone_like_source_url_path_is_rejected(self):
        brief = deepcopy(self.brief)
        brief["evidence_sources"][0]["source_url"] = "https://example.com/contact/212-555-0199"
        self.assert_code("pii_phone", brief=brief, path="$.evidence_sources[0].source_url")

    def test_onet_occupation_code_source_url_is_accepted(self):
        brief = deepcopy(self.brief)
        brief["evidence_sources"][0]["source_url"] = (
            "https://www.onetonline.org/link/details/11-2021.00"
        )
        errors = self.errors(brief=brief)
        self.assertNotIn("pii_phone", {error.code for error in errors}, errors)

    def test_malformed_nested_collections_never_raise(self):
        mutations = []
        panel = deepcopy(self.panel)
        panel["segments"] = None
        mutations.append((self.brief, panel))
        panel = deepcopy(self.panel)
        panel["persona_research"]["evidence_ids"] = None
        mutations.append((self.brief, panel))
        brief = deepcopy(self.brief)
        brief["segment_hypotheses"][0]["finding_ids"] = [{"not": "a string"}]
        mutations.append((brief, self.panel))
        for brief, panel in mutations:
            with self.subTest(panel_segments=panel.get("segments")):
                errors = self.errors(brief=brief, panel=panel)
                self.assertTrue(errors)
                self.assertTrue(all(hasattr(error, "code") for error in errors))

    def test_nonobject_provenance_entries_are_rejected(self):
        panel = deepcopy(self.panel)
        panel["context_strata"][0]["dimensions"] = ["buying-stage"]
        self.assert_code("invalid_type", panel=panel, path="$.context_strata[0].dimensions[0]")
        panel = deepcopy(self.panel)
        panel["grounded_context_profiles"][0]["context_attribute_provenance"] = [42]
        self.assert_code("invalid_type", panel=panel, path="$.grounded_context_profiles[0].context_attribute_provenance[0]")

    def test_grounded_snapshot_role_and_decision_match_archetype(self):
        panel = deepcopy(self.panel)
        panel["grounded_context_profiles"][0]["profile_snapshot"]["role_context"] = "Unsupported role"
        self.assert_code("unsupported_profile_variation", panel=panel, path="$.grounded_context_profiles[0].profile_snapshot.role_context")
        panel = deepcopy(self.panel)
        panel["grounded_context_profiles"][0]["profile_snapshot"]["decision_context"] = "Unsupported decision"
        self.assert_code("unsupported_profile_variation", panel=panel, path="$.grounded_context_profiles[0].profile_snapshot.decision_context")

    def test_grounded_profile_provenance_stays_with_selected_segment(self):
        brief = deepcopy(self.brief)
        second_evidence = deepcopy(brief["evidence_sources"][0])
        second_evidence["evidence_id"] = "evidence-2"
        brief["evidence_sources"].append(second_evidence)
        second_finding = deepcopy(brief["findings"][0])
        second_finding.update(finding_id="finding-2", evidence_ids=["evidence-2"])
        brief["findings"].append(second_finding)
        panel = deepcopy(self.panel)
        panel["persona_research"]["evidence_ids"].append("evidence-2")
        profile = deepcopy(panel["grounded_context_profiles"][0])
        profile["grounded_profile_id"] = "unsupported-manual-profile"
        profile["context_attribute_provenance"][0].update(source_evidence=["evidence-2"], finding_ids=["finding-2"])
        panel["grounded_context_profiles"].append(profile)
        self.assert_code("unsupported_segment_provenance", brief=brief, panel=panel, path="$.panel.grounded_context_profiles[1].context_attribute_provenance[0]")

    def test_weights_must_be_finite(self):
        for value in (float("nan"), float("inf"), float("-inf")):
            panel = deepcopy(self.panel)
            panel["segments"][0]["study_weight"] = value
            self.assert_code("invalid_study_weight", panel=panel)
            panel = deepcopy(self.panel)
            panel["context_strata"][0]["planned_weight"] = value
            self.assert_code("invalid_planned_weight", panel=panel)

    def test_provisional_standalone_panel_rejects_claimed_evidence(self):
        panel = deepcopy(self.panel)
        panel["persona_research"].update(
            status="provisional_no_research", mode="provisional_no_research",
            source_state="no_research_sources", source_types=[],
            expires_at="2026-07-30T12:00:00Z",
        )
        self.assertIn(
            "provisional_evidence_mismatch",
            {error.code for error in validate_saved_panel(panel, now=datetime(2026, 7, 22, tzinfo=timezone.utc))},
        )

    def test_research_mode_depth_and_confidence_enums_are_strict(self):
        brief = deepcopy(self.brief)
        brief["research_mode"] = "made_up"
        self.assert_code("invalid_research_mode", brief=brief)
        brief = deepcopy(self.brief)
        brief["research_depth"] = "focused"
        self.assert_code("invalid_research_depth", brief=brief)
        brief = deepcopy(self.brief)
        brief["findings"][0]["confidence"] = "fairly_sure"
        self.assert_code("invalid_confidence", brief=brief)
        brief = deepcopy(self.brief)
        brief["findings"][0]["category"] = "psychographics"
        self.assert_code("invalid_finding_category", brief=brief)

    def test_approved_and_provisional_modes_cannot_cross(self):
        brief = deepcopy(self.brief)
        brief["research_mode"] = "provisional_no_research"
        self.assert_code("approved_mode_mismatch", brief=brief)
        brief = deepcopy(self.brief)
        brief["status"] = "provisional_no_research"
        self.assert_code("provisional_mode_mismatch", brief=brief)
        self.assert_code("provisional_research_content", brief=brief)

    def test_provisional_must_clear_research_coverage_and_confidence_claims(self):
        brief = deepcopy(self.brief)
        brief.update(status="provisional_no_research", research_mode="provisional_no_research", research_questions=[], evidence_sources=[], findings=[])
        brief["segment_hypotheses"][0].update(origin="provisional_user_defined", finding_ids=[], evidence_ids=[])
        self.assert_code("provisional_coverage_mismatch", brief=brief)
        self.assert_code("provisional_confidence_mismatch", brief=brief)

        panel = deepcopy(self.panel)
        panel["persona_research"].update(
            status="provisional_no_research", mode="provisional_no_research",
            source_state="no_research_sources", source_types=[], evidence_ids=[],
            expires_at="2026-07-30T12:00:00Z",
        )
        panel["segments"][0].update(origin="provisional_user_defined", weighting_rule="planning_allocation", weight_source_evidence=[], finding_ids=[], evidence_ids=[])
        panel["persona_archetypes"][0].update(finding_ids=[], evidence_ids=[])
        panel["context_strata"][0]["dimensions"][0].update(status="experimental", source_evidence=[], finding_ids=[])
        panel["grounded_context_profiles"][0]["context_attribute_provenance"][0].update(status="experimental", source_evidence=[], finding_ids=[])
        standalone_codes = {error.code for error in validate_saved_panel(panel, now=datetime(2026, 7, 22, tzinfo=timezone.utc))}
        self.assertIn("provisional_coverage_mismatch", standalone_codes)
        self.assertIn("provisional_confidence_mismatch", standalone_codes)

    def test_finding_evidence_cannot_be_laundered_at_any_level(self):
        brief = deepcopy(self.brief)
        evidence = deepcopy(brief["evidence_sources"][0])
        evidence["evidence_id"] = "evidence-2"
        brief["evidence_sources"].append(evidence)
        finding = deepcopy(brief["findings"][0])
        finding.update(finding_id="finding-2", evidence_ids=["evidence-2"])
        brief["findings"].append(finding)
        bad_brief = deepcopy(brief)
        bad_brief["segment_hypotheses"][0]["evidence_ids"] = ["evidence-2"]
        self.assert_code("finding_evidence_mismatch", brief=bad_brief)

        base_panel = deepcopy(self.panel)
        base_panel["persona_research"]["evidence_ids"].append("evidence-2")
        for label, mutate in (
            ("segment", lambda p: p["segments"][0].update(evidence_ids=["evidence-2"])),
            ("archetype", lambda p: p["persona_archetypes"][0].update(evidence_ids=["evidence-2"])),
            ("stratum", lambda p: p["context_strata"][0]["dimensions"][0].update(source_evidence=["evidence-2"])),
            ("profile", lambda p: p["grounded_context_profiles"][0]["context_attribute_provenance"][0].update(source_evidence=["evidence-2"])),
        ):
            panel = deepcopy(base_panel)
            mutate(panel)
            with self.subTest(level=label):
                self.assert_code("finding_evidence_mismatch", brief=brief, panel=panel)

    def test_grounded_provenance_matches_entire_stratum_record_and_rejects_duplicates(self):
        for key, value in (("status", "estimated"), ("source_evidence", []), ("finding_ids", [])):
            panel = deepcopy(self.panel)
            panel["grounded_context_profiles"][0]["context_attribute_provenance"][0][key] = value
            self.assert_code("profile_provenance_mismatch", panel=panel)
        panel = deepcopy(self.panel)
        panel["grounded_context_profiles"][0]["context_attribute_provenance"].append(
            deepcopy(panel["grounded_context_profiles"][0]["context_attribute_provenance"][0])
        )
        self.assert_code("duplicate_provenance", panel=panel)

    def test_public_validators_are_total_over_recursive_json_shape_mutations(self):
        def paths(value, prefix=()):
            if isinstance(value, dict):
                for key, child in value.items():
                    yield prefix + (key,)
                    yield from paths(child, prefix + (key,))
            elif isinstance(value, list):
                for index, child in enumerate(value):
                    yield prefix + (index,)
                    yield from paths(child, prefix + (index,))

        def replace(value, path, replacement):
            clone = deepcopy(value)
            cursor = clone
            for part in path[:-1]:
                cursor = cursor[part]
            cursor[path[-1]] = replacement
            return clone

        replacements = (None, False, 7, "wrong-shape", [], {})
        for document_name, original in (("brief", self.brief), ("panel", self.panel)):
            for path in paths(original):
                for replacement in replacements:
                    mutated = replace(original, path, replacement)
                    brief = mutated if document_name == "brief" else self.brief
                    panel = mutated if document_name == "panel" else self.panel
                    errors = validate_audience_research_pair(brief, panel, now=datetime(2026, 7, 22, tzinfo=timezone.utc))
                    self.assertIsInstance(errors, list, (document_name, path, replacement))
                    self.assertTrue(all(hasattr(error, "code") for error in errors))
        for scalar in (None, False, 1, "text", [], [1], {}):
            self.assertIsInstance(validate_audience_research_pair(scalar, scalar), list)
            self.assertIsInstance(compute_scope_fingerprint(scalar), str)

    def test_refresh_and_governance_are_required_and_strict(self):
        panel = deepcopy(self.panel)
        del panel["refresh_conditions"]["triggers"]
        self.assert_code("missing_field", panel=panel, path="$.refresh_conditions.triggers")
        panel = deepcopy(self.panel)
        panel["governance"]["privacy_confirmation"]["confirmed"] = False
        self.assert_code("privacy_not_confirmed", panel=panel)

    def test_structured_exception_preserves_stable_errors(self):
        panel = deepcopy(self.panel)
        panel["version"] = "one"
        with self.assertRaises(AudienceResearchValidationError) as caught:
            require_valid_audience_research_pair(self.brief, panel)
        self.assertEqual("invalid_version", caught.exception.errors[0].code)
        self.assertSetEqual({"code", "field", "message"}, set(caught.exception.errors[0].to_dict()))


if __name__ == "__main__":
    unittest.main()
