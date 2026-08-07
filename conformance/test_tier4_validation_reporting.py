from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "skills" / "audience-panel-builder" / "scripts"))

from audience_panel_builder.population.validation.reporting import (  # noqa: E402
    build_validation_report_payload as _build_validation_report_payload,
    render_validation_report,
)
from conformance.test_tier4_held_out_evaluation import (  # noqa: E402
    _AUTHORITY_REGISTRIES, build_claim_family, comparison,
    evaluate_held_out_ordering, issue_tier4_claim, sealed_registration,
)


def build_validation_report_payload(**kwargs: object) -> dict[str, object]:
    return _build_validation_report_payload(
        **kwargs, authority_registry=_AUTHORITY_REGISTRIES,
    )


def positive_evaluation() -> tuple[dict[str, object], dict[str, object]]:
    registration = sealed_registration()
    comparisons = [comparison(registration, index) for index in range(12)]
    family = build_claim_family(
        registrations=[registration],
        comparisons_by_registration={registration["registration_id"]: comparisons},
        built_at="2026-09-01T00:00:00Z",
    )
    return registration, evaluate_held_out_ordering(
        registration=registration, comparisons=comparisons, claim_family=family,
        evaluated_at="2026-09-01T00:00:00Z",
    )


def lifecycle_record(
    claim: dict[str, object],
    evaluation: dict[str, object],
    status: str,
) -> dict[str, object]:
    from audience_panel_builder.common import sha256_json

    return {
        "status": "ok",
        "claim": {
            "claim_id": claim["claim_id"],
            "claim_sha256": claim["claim_sha256"],
            "panel_id": evaluation["panel_binding"]["panel_id"],
            "panel_version": evaluation["panel_binding"]["panel_version"],
            "claim_scope_sha256": sha256_json(evaluation["claim_scope"]),
            "package_sha256": "package-sha",
            "package_manifest_sha256": "manifest-sha",
        },
        "lifecycle_status": status,
        "lifecycle_event": None,
        "as_of": "2026-10-01T00:00:00Z",
    }


class Tier4ValidationReportingTests(unittest.TestCase):
    def test_marketer_summary_leads_with_what_happened(self) -> None:
        registration, evaluation = positive_evaluation()
        claim = issue_tier4_claim(
            evaluation=evaluation, issued_at="2026-09-01T01:00:00Z",
            expires_at="2027-03-01T00:00:00Z",
        )
        payload = build_validation_report_payload(
            registration=registration, evaluation=evaluation, claim=claim,
        )
        self.assertEqual("Held-out ordering validation", payload["headline"])
        self.assertIn("the panel generally put the stronger-performing ads higher", payload["plain_language_summary"])
        self.assertNotIn("ordinal concordance", payload["plain_language_summary"])
        self.assertNotIn("predictive accuracy", json.dumps(payload))
        self.assertEqual("operations-leaders", payload["scope"][0]["value"])
        self.assertEqual("1-0-0", payload["scope"][1]["value"])
        self.assertEqual("enterprise", payload["scope"][6]["value"])
        self.assertEqual("2027-03-01T00:00:00Z", payload["expires_at"])
        self.assertFalse(payload["active_claim"])
        self.assertEqual("unregistered", payload["claim_status"])

    def test_expired_and_inactive_claims_never_create_an_active_report_section(self) -> None:
        from conformance.test_tier4_validation_library import (
            append_claim_lifecycle_event, register_validation_package,
        )
        from conformance.test_tier4_validation_package import (
            build_validation_package,
        )
        from conformance.test_tier4_validation_package import (
            Tier4ValidationPackageTests,
        )
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            helper = Tier4ValidationPackageTests()
            panel = helper._panel(root / "panel")
            inputs = helper._inputs(root / "inputs", panel)
            package = build_validation_package(
                inputs=inputs,
                panel_package_path=panel,
                output_dir=root / "out",
            )
            registration = json.loads(
                inputs["panel-validation-preregistration.json"].read_text()
            )
            evaluation = json.loads(
                inputs["panel-held-out-evaluation.json"].read_text()
            )
            claim = json.loads(inputs["panel-tier4-claim.json"].read_text())

            expiry_library = root / "expiry-library"
            register_validation_package(
                package,
                library_root=expiry_library,
                registered_at="2026-09-02T00:00:00Z",
            )
            expired = build_validation_report_payload(
                registration=registration,
                evaluation=evaluation,
                claim=claim,
                as_of="2027-03-01T00:00:00Z",
                library_root=expiry_library,
                validation_package_path=package,
            )
            self.assertFalse(expired["active_claim"])
            self.assertEqual("expired", expired["claim_status"])

            withdrawn_library = root / "withdrawn-library"
            registered = register_validation_package(
                package,
                library_root=withdrawn_library,
                registered_at="2026-09-02T00:00:00Z",
            )["claim"]
            append_claim_lifecycle_event(
                claim_id=registered["claim_id"],
                event_type="withdrawn",
                effective_at="2026-10-01T00:00:00Z",
                actor_id="maintainer-001",
                reason="Evidence authority withdrew the claim.",
                evidence_sha256=[registered["claim_sha256"]],
                replacement_claim_id=None,
                library_root=withdrawn_library,
            )
            withdrawn = build_validation_report_payload(
                registration=registration,
                evaluation=evaluation,
                claim=claim,
                as_of="2026-10-02T00:00:00Z",
                library_root=withdrawn_library,
                validation_package_path=package,
            )
            self.assertFalse(withdrawn["active_claim"])
            self.assertEqual("withdrawn", withdrawn["claim_status"])

    def test_lifecycle_status_remains_authoritative_after_expiry(self) -> None:
        registration, evaluation = positive_evaluation()
        claim = issue_tier4_claim(
            evaluation=evaluation, issued_at="2026-09-01T01:00:00Z",
            expires_at="2027-03-01T00:00:00Z",
        )
        for lifecycle_status in ("expired", "superseded", "withdrawn", "invalidated"):
            with patch(
                "audience_panel_builder.population.validation.library.claim_lifecycle_status",
                return_value=lifecycle_record(
                    claim, evaluation, lifecycle_status,
                ),
            ), patch(
                "audience_panel_builder.population.validation.package.validate_validation_package",
                return_value={
                    "claim": claim,
                    "evaluation": evaluation,
                    "package_zip_sha256": "package-sha",
                    "package_manifest_sha256": "manifest-sha",
                },
            ):
                payload = build_validation_report_payload(
                    registration=registration,
                    evaluation=evaluation,
                    claim=claim,
                    as_of="2028-01-01T00:00:00Z",
                    library_root=Path("/authoritative-library"),
                    validation_package_path=Path("/validation-package.zip"),
                )
            self.assertTrue(payload["claim_expired"])
            self.assertFalse(payload["active_claim"])
            self.assertEqual(lifecycle_status, payload["claim_status"])
        transitioned = dict(claim)
        transitioned["status"] = "withdrawn"
        from audience_panel_builder.common import ContractError, sha256_json
        transitioned["claim_sha256"] = None
        transitioned["claim_sha256"] = sha256_json(transitioned)
        with self.assertRaisesRegex(
            ContractError, "canonical claim issuance|lifecycle status",
        ):
            build_validation_report_payload(
                registration=registration,
                evaluation=evaluation,
                claim=transitioned,
            )

    def test_negative_limited_and_invalid_states_are_first_class(self) -> None:
        for status, headline in (
            ("tier4_not_supported", "The result did not support Tier 4"),
            ("evaluated_with_limitations", "Not enough evidence yet"),
            ("invalid", "The validation could not be used"),
        ):
            registration = sealed_registration()
            comparisons = [
                comparison(
                    registration, index,
                    reverse=status == "tier4_not_supported",
                )
                for index in range(12)
            ]
            if status == "evaluated_with_limitations":
                comparisons = comparisons[:5]
            family = build_claim_family(
                registrations=[registration],
                comparisons_by_registration={
                    registration["registration_id"]: comparisons,
                },
                built_at="2026-09-01T00:00:00Z",
            )
            candidate = evaluate_held_out_ordering(
                registration=registration,
                comparisons=comparisons,
                claim_family=family,
                evaluated_at=(
                    "2026-07-01T00:00:00Z"
                    if status == "invalid"
                    else "2026-09-01T00:00:00Z"
                ),
            )
            self.assertEqual(status, candidate["decision"]["status"])
            payload = build_validation_report_payload(
                registration=registration, evaluation=candidate, claim=None,
            )
            self.assertEqual(headline, payload["headline"])
            self.assertFalse(payload["active_claim"])

    def test_rendered_report_is_self_contained_and_script_safe(self) -> None:
        registration, evaluation = positive_evaluation()
        claim = issue_tier4_claim(
            evaluation=evaluation, issued_at="2026-09-01T01:00:00Z",
            expires_at="2027-03-01T00:00:00Z",
        )
        payload = build_validation_report_payload(registration=registration, evaluation=evaluation, claim=claim)
        payload["limitations"] = ["<img src=x onerror=alert(1)>"]
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "report.html"
            render_validation_report(
                payload=payload,
                template_path=ROOT / "skills" / "audience-panel-builder" / "assets" / "panel-validation-report-template.html",
                output_path=output,
            )
            html = output.read_text(encoding="utf-8")
            second = Path(temporary) / "second.html"
            with patch(
                "audience_panel_builder.population.validation.library.claim_lifecycle_status",
                return_value=lifecycle_record(claim, evaluation, "active"),
            ), patch(
                "audience_panel_builder.population.validation.library.current_claim",
                return_value={
                    "claim": {
                        "claim_id": claim["claim_id"],
                    },
                },
            ), patch(
                "audience_panel_builder.population.validation.package.validate_validation_package",
                return_value={
                    "claim": claim,
                    "evaluation": evaluation,
                    "package_zip_sha256": "package-sha",
                    "package_manifest_sha256": "manifest-sha",
                },
            ):
                active = build_validation_report_payload(
                    registration=registration,
                    evaluation=evaluation,
                    claim=claim,
                    as_of="2026-10-01T00:00:00Z",
                    library_root=Path("/authoritative-library"),
                    validation_package_path=Path("/validation-package.zip"),
                )
            render_validation_report(
                payload=active,
                template_path=ROOT / "skills" / "audience-panel-builder" / "assets" / "panel-validation-report-template.html",
                output_path=second,
            )
            active_html = second.read_text(encoding="utf-8")
            with self.assertRaises(FileExistsError):
                render_validation_report(
                    payload=active,
                    template_path=ROOT / "skills" / "audience-panel-builder" / "assets" / "panel-validation-report-template.html",
                    output_path=second,
                )
        self.assertIn("&lt;img", html)
        self.assertNotIn("<script", html)
        self.assertNotIn("innerHTML", html)
        self.assertIn("Technical audit trail", html)
        self.assertIn("Active claim details", active_html)
        self.assertIn("Applies only to the registered panel", active_html)
        self.assertIn("Expires: 2027-03-01T00:00:00Z", active_html)
        self.assertIn("Refresh triggers", active_html)


if __name__ == "__main__":
    unittest.main()
