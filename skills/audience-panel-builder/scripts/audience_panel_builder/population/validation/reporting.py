"""Plain-language, self-contained reports for authenticated Tier 4 results.

The report is deliberately a projection of already validated documents.  It
does not import the statistics module or derive a second result from the
evaluation's diagnostics.
"""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from html import escape
import json
import os
from pathlib import Path
from typing import Mapping

from ...common import ContractError, sha256_json
from .contracts import (
    authenticate_preregistration_design,
    validate_held_out_evaluation,
    validate_preregistration,
    validate_tier4_claim,
)


REPORT_DATA_PLACEHOLDER = "__PANEL_VALIDATION_REPORT_DATA__"
_NON_PREDICTION = (
    "This does not predict click-through rate, conversion rate, revenue, a "
    "winning probability, or causal lift. It does not replace live testing."
)


def _status_copy(status: str) -> tuple[str, str]:
    return {
        "tier4_supported": (
            "Held-out ordering validation",
            "Across the qualifying independent real-world tests, the panel generally "
            "put the stronger-performing ads higher in its ranking.",
        ),
        "tier4_not_supported": (
            "The result did not support Tier 4",
            "The qualifying evidence did not show enough agreement between the panel "
            "ranking and the held-out real-world results to support Tier 4.",
        ),
        "evaluated_with_limitations": (
            "Not enough evidence yet",
            "The evaluation could not establish a Tier 4 result because the available "
            "evidence did not meet every required condition.",
        ),
        "invalid": (
            "The validation could not be used",
            "The validation evidence was leaked, mismatched, or otherwise could not be "
            "used for a Tier 4 conclusion.",
        ),
    }[status]


def _scope_rows(scope: Mapping[str, object], metric: Mapping[str, object]) -> list[dict[str, str]]:
    outcome = scope["outcome_scope"]
    panel = scope["panel_binding"]
    synthetic = scope["synthetic_binding"]
    assert isinstance(outcome, Mapping)
    assert isinstance(panel, Mapping) and isinstance(synthetic, Mapping)
    rows = [
        ("Panel", panel["panel_id"]),
        ("Panel version", panel["panel_version"]),
        ("Synthetic surface", synthetic["surface"]),
        ("Synthetic run", synthetic["run_id"]),
        ("Synthetic result", synthetic["result_sha256"]),
        ("Cohort", outcome["cohort_id"]),
        ("Audience segment", outcome["segment_id"]),
        ("Channel", outcome["channel"]),
        ("Placement", outcome["placement"]),
        ("Campaign objective", outcome["objective"]),
        ("Geography", outcome["geography"]),
        ("Validation window", outcome["validation_window"]),
        ("Outcome metric", metric["name"]),
    ]
    return [{"label": label, "value": str(value)} for label, value in rows]


def _as_of(value: datetime | str | None) -> datetime:
    if value is None:
        return datetime.now(timezone.utc)
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ContractError("as_of must be an ISO 8601 timestamp") from exc
    if value.tzinfo is None or value.utcoffset() is None:
        raise ContractError("as_of must include a timezone")
    return value.astimezone(timezone.utc)


def build_validation_report_payload(
    *,
    registration: dict[str, object],
    evaluation: dict[str, object],
    claim: dict[str, object] | None,
    as_of: datetime | str | None = None,
    library_root: Path | None = None,
    validation_package_path: Path | None = None,
    authority_registry: object,
) -> dict[str, object]:
    """Project a closed C1 evaluation into marketer-first report data."""

    registered, approval = authenticate_preregistration_design(
        registration, authority_registry=authority_registry,
    )
    evaluated = validate_held_out_evaluation(evaluation)
    from .evaluation import evaluate_held_out_ordering

    replayed_evaluation = evaluate_held_out_ordering(
        registration=registered,
        comparisons=evaluated["comparisons"],
        claim_family=evaluated["claim_family"],
        evaluated_at=str(evaluated["evaluated_at"]),
        design_approval=approval,
        authority_registry=authority_registry,
    )
    if evaluated != replayed_evaluation:
        raise ContractError(
            "report evaluation must exactly equal the result replayed from "
            "authenticated preregistration and comparison evidence"
        )
    if evaluated["registration_binding"] != {
        "registration_id": registered["registration_id"],
        "registration_sha256": registered["registration_sha256"],
    }:
        raise ContractError("evaluation does not bind the supplied registration")
    if evaluated["panel_binding"] != registered["panel_binding"]:
        raise ContractError("evaluation panel binding does not match registration")
    if evaluated["claim_scope"] != registered["claim_scope"]:
        raise ContractError("evaluation claim scope does not match registration")

    checked_claim: dict[str, object] | None = None
    if claim is not None:
        checked_claim = validate_tier4_claim(claim)
        from .evaluation import issue_tier4_claim

        replayed_claim = issue_tier4_claim(
            evaluation=evaluated,
            issued_at=str(checked_claim["issued_at"]),
            expires_at=str(checked_claim["expires_at"]),
            design_approval=approval,
            authority_registry=authority_registry,
        )
        if checked_claim != replayed_claim:
            raise ContractError(
                "report claim must exactly equal canonical claim issuance"
            )
        if checked_claim["status"] != "active":
            raise ContractError(
                "packaged claim status must remain active; lifecycle status "
                "comes from the authoritative validation library"
            )
        if checked_claim["panel_binding"] != evaluated["panel_binding"]:
            raise ContractError("claim panel binding does not match evaluation")
        if checked_claim["claim_scope"] != evaluated["claim_scope"]:
            raise ContractError("claim scope does not match evaluation")
        if checked_claim["evaluation_binding"] != {
            "evaluation_id": evaluated["evaluation_id"],
            "evaluation_sha256": evaluated["evaluation_sha256"],
        }:
            raise ContractError("claim does not bind the supplied evaluation")

    status = str(evaluated["decision"]["status"])
    headline, summary = _status_copy(status)
    checked_as_of = _as_of(as_of)
    claim_expires_at = checked_claim["expires_at"] if checked_claim is not None else None
    chronological_expiry = (
        checked_claim is not None
        and datetime.fromisoformat(str(claim_expires_at).replace("Z", "+00:00")) <= checked_as_of
    )
    lifecycle_record: dict[str, object] | None = None
    if checked_claim is not None and library_root is not None:
        from .library import (
            LibraryNotFoundError,
            claim_lifecycle_status,
            current_claim,
        )
        from .package import validate_validation_package

        if validation_package_path is None:
            raise ContractError(
                "an authoritative active/inactive claim report requires the "
                "exact registered validation package"
            )
        validated_package = validate_validation_package(
            Path(validation_package_path),
            authority_registry=authority_registry,
        )
        if (
            validated_package.get("claim") != checked_claim
            or validated_package.get("evaluation") != evaluated
        ):
            raise ContractError(
                "validation package does not contain the supplied exact "
                "claim and evaluation"
            )

        lifecycle_record = claim_lifecycle_status(
            str(checked_claim["claim_id"]),
            library_root=Path(library_root),
            as_of=checked_as_of.isoformat().replace("+00:00", "Z"),
            authority_registry=authority_registry,
        )
        registered_claim = lifecycle_record["claim"]
        assert isinstance(registered_claim, Mapping)
        expected_registered_identity = {
            "claim_id": checked_claim["claim_id"],
            "claim_sha256": checked_claim["claim_sha256"],
            "panel_id": evaluated["panel_binding"]["panel_id"],
            "panel_version": evaluated["panel_binding"]["panel_version"],
            "package_sha256": validated_package["package_zip_sha256"],
            "package_manifest_sha256": (
                validated_package["package_manifest_sha256"]
            ),
        }
        if any(
            registered_claim.get(key) != value
            for key, value in expected_registered_identity.items()
        ):
            raise ContractError(
                "authoritative validation library does not bind the supplied "
                "exact registered package, claim, panel, and version"
            )
        if (
            registered_claim.get("claim_scope_sha256")
            != sha256_json(evaluated["claim_scope"])
        ):
            raise ContractError(
                "authoritative validation library does not bind the supplied "
                "claim scope"
            )
        claim_status = str(lifecycle_record["lifecycle_status"])
        if claim_status == "active":
            try:
                authoritative_current = current_claim(
                    str(evaluated["panel_binding"]["panel_id"]),
                    str(evaluated["panel_binding"]["panel_version"]),
                    sha256_json(evaluated["claim_scope"]),
                    library_root=Path(library_root),
                    as_of=checked_as_of.isoformat().replace("+00:00", "Z"),
                    authority_registry=authority_registry,
                )
            except LibraryNotFoundError:
                claim_status = "not_current"
            else:
                current_entry = authoritative_current["claim"]
                assert isinstance(current_entry, Mapping)
                if current_entry.get("claim_id") != checked_claim["claim_id"]:
                    claim_status = "not_current"
    elif checked_claim is not None:
        claim_status = "unregistered"
    else:
        claim_status = "not_issued"
    claim_is_expired = chronological_expiry or claim_status == "expired"
    active_claim = (
        checked_claim is not None
        and status == "tier4_supported"
        and claim_status == "active"
    )
    scope = evaluated["claim_scope"]
    metric = evaluated["metric_binding"]
    assert isinstance(scope, Mapping) and isinstance(metric, Mapping)
    segments = evaluated["segment_diagnostics"]
    assert isinstance(segments, list)
    segment_states = [str(item.get("status", "limitations")) for item in segments if isinstance(item, Mapping)]
    segment_summary = (
        "Important audience groups met the registered agreement checks."
        if segment_states and all(state == "pass" for state in segment_states)
        else "Important audience groups had limitations or disagreement; the claim does not extend beyond the exact registered scope."
    )
    qualifying_blocks = sum(
        1 for comparison in evaluated["comparisons"]
        if isinstance(comparison, Mapping)
        and all(
            isinstance(observation, Mapping)
            and observation.get("holdout_status") == "eligible_held_out"
            for observation in comparison.get("observations", [])
        )
    )
    submitted_blocks = len(evaluated["block_inventory"])
    what_we_tested = (
        f"A frozen panel ordering was compared with {qualifying_blocks} "
        "qualifying independent real-world tests using the registered "
        "outcome metric."
        if status in {"tier4_supported", "tier4_not_supported"}
        else (
            f"{submitted_blocks} real-world test blocks were submitted; "
            f"{qualifying_blocks} met the registered held-out eligibility "
            "conditions. The result remains limited."
        )
        if status == "evaluated_with_limitations"
        else (
            f"{submitted_blocks} real-world test blocks were submitted, but "
            "the evidence could not be treated as qualifying independent "
            "validation."
        )
    )
    return {
        "schema_version": "panel-tier4-validation-report-payload-v1",
        "headline": headline,
        "plain_language_summary": summary,
        "status": status,
        "active_claim": active_claim,
        "claim_status": claim_status,
        "claim_expired": claim_is_expired,
        "claim_status_label": {
            "active": "Active narrow claim",
            "expired": "Claim expired",
            "superseded": "Claim superseded",
            "withdrawn": "Claim withdrawn",
            "invalidated": "Claim invalidated",
            "not_yet_active": "Claim not active yet",
            "not_current": "Claim registered but not current",
            "unregistered": "Claim issued but not registered",
            "not_issued": "No claim was issued",
        }[claim_status],
        "what_we_tested": what_we_tested,
        "scope": _scope_rows(scope, metric),
        "segment_summary": segment_summary,
        "non_prediction_disclaimer": _NON_PREDICTION,
        "limitations": [str(item) for item in evaluated["limitations"]],
        "refresh_triggers": (
            [str(item) for item in checked_claim["refresh_triggers"]]
            if checked_claim is not None else []
        ),
        "expires_at": claim_expires_at,
        "claim_text": checked_claim["claim_text"] if checked_claim is not None else None,
        "claim_disclaimer": checked_claim["required_disclaimer"] if checked_claim is not None else None,
        "technical": {
            "registration_id": registered["registration_id"],
            "registration_sha256": registered["registration_sha256"],
            "evaluation_id": evaluated["evaluation_id"],
            "evaluation_sha256": evaluated["evaluation_sha256"],
            "panel_binding": deepcopy(evaluated["panel_binding"]),
            "synthetic_binding": deepcopy(scope["synthetic_binding"]),
            "metric": deepcopy(metric),
            "block_inventory": deepcopy(evaluated["block_inventory"]),
            "gate_results": deepcopy(evaluated["gate_results"]),
            "diagnostics": {
                key: deepcopy(evaluated[key])
                for key in (
                    "coverage", "missingness", "sample_sufficiency", "independence",
                    "leakage", "multiplicity", "repeated_looks", "power",
                    "overall_diagnostics",
                    "segment_diagnostics", "influence_diagnostics",
                )
            },
            "claim_id": checked_claim["claim_id"] if checked_claim else None,
            "claim_sha256": checked_claim["claim_sha256"] if checked_claim else None,
            "library_lifecycle": deepcopy(lifecycle_record),
        },
    }


def _text(value: object | None, *, fallback: str = "Not available") -> str:
    return escape(fallback if value is None or value == "" else str(value))


def _static_report_body(payload: Mapping[str, object]) -> str:
    scope = payload.get("scope")
    scope_rows = scope if isinstance(scope, list) else []
    scope_html = "".join(
        f"<dt>{_text(row.get('label'))}</dt><dd>{_text(row.get('value'))}</dd>"
        for row in scope_rows
        if isinstance(row, Mapping)
    )
    limitations = payload.get("limitations")
    limitation_values = limitations if isinstance(limitations, list) else []
    limitations_text = " ".join(str(item) for item in limitation_values)
    technical = payload.get("technical")
    technical_json = json.dumps(
        technical if isinstance(technical, Mapping) else {},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    block_inventory = (
        technical.get("block_inventory", [])
        if isinstance(technical, Mapping) else []
    )
    block_count = len(block_inventory) if isinstance(block_inventory, list) else 0
    active_claim_html = ""
    if payload.get("active_claim") is True:
        refresh = payload.get("refresh_triggers")
        refresh_values = refresh if isinstance(refresh, list) else []
        refresh_text = "; ".join(str(item) for item in refresh_values)
        active_claim_html = (
            "<div id=\"active-claim\"><h3>Active claim details</h3>"
            f"<p>{_text(payload.get('claim_text'))}</p>"
            f"<p>{_text(payload.get('claim_disclaimer'))}</p>"
            f"<p>Expires: {_text(payload.get('expires_at'))}</p>"
            f"<p>Refresh triggers: {_text(refresh_text, fallback='None recorded')}</p>"
            "</div>"
        )
    elif payload.get("expires_at"):
        active_claim_html = (
            "<div id=\"active-claim\"><p>Recorded expiry: "
            f"{_text(payload.get('expires_at'))}</p></div>"
        )
    claim_text = (
        payload.get("claim_text")
        if payload.get("active_claim") is True
        else payload.get("plain_language_summary")
    )
    return (
        "<main>"
        "<p>Audience Panel validation</p>"
        f"<h1 id=\"headline\">{_text(payload.get('headline'))}</h1>"
        f"<p id=\"summary\" class=\"notice\">{_text(payload.get('plain_language_summary'))}</p>"
        "<section><h2>What we tested</h2>"
        f"<p id=\"tested\">{_text(payload.get('what_we_tested'))}</p></section>"
        "<section><h2>What happened</h2>"
        f"<p id=\"claim-status\">{_text(payload.get('claim_status_label'))}</p>"
        f"<p id=\"claim\">{_text(claim_text)}</p>{active_claim_html}"
        f"<p id=\"limits\">{_text(limitations_text, fallback='None recorded')}</p></section>"
        f"<section><h2>Where this applies</h2><dl id=\"scope\">{scope_html}</dl></section>"
        "<section><h2>Important audience groups</h2>"
        f"<p id=\"segments\">{_text(payload.get('segment_summary'))}</p></section>"
        "<section><h2>What this does not predict</h2>"
        f"<p id=\"non-prediction\">{_text(payload.get('non_prediction_disclaimer'))}</p></section>"
        "<section><h2>Evidence and methodology</h2>"
        f"<p id=\"method\">The registered evaluation reviewed {block_count} test blocks. "
        "Statistical detail is retained below for audit.</p></section>"
        "<section><h2>Technical audit trail</h2>"
        f"<pre id=\"technical\">{escape(technical_json)}</pre></section>"
        "</main>"
    )


def render_validation_report_bytes(
    *, payload: dict[str, object], template_path: Path,
) -> bytes:
    """Render deterministic report bytes without publishing a file."""

    template = Path(template_path).read_text(encoding="utf-8")
    if template.count(REPORT_DATA_PLACEHOLDER) != 1:
        raise ValueError(
            "validation report template must contain one data placeholder"
        )
    rendered = template.replace(
        REPORT_DATA_PLACEHOLDER, _static_report_body(payload), 1,
    )
    if "<script" in rendered.lower() or "innerHTML" in rendered:
        raise ValueError("validation report template must remain static HTML")
    return rendered.encode("utf-8")


def render_validation_report(*, payload: dict[str, object], template_path: Path, output_path: Path) -> Path:
    """Render one no-network HTML report without interpolating untrusted HTML."""

    output = Path(output_path)
    rendered = render_validation_report_bytes(
        payload=payload, template_path=template_path,
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    try:
        descriptor = os.open(output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        raise FileExistsError("validation report output already exists") from None
    with os.fdopen(descriptor, "wb") as handle:
        handle.write(rendered)
    return output


__all__ = [
    "REPORT_DATA_PLACEHOLDER",
    "build_validation_report_payload",
    "render_validation_report",
    "render_validation_report_bytes",
]
