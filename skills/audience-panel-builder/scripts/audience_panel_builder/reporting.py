"""Deterministic, evidence-derived Audience Panel Builder research reports."""

from __future__ import annotations

from collections import Counter, defaultdict
from html import escape
import hashlib
from pathlib import Path
import re
import sys
from typing import Any, Mapping


SIBLING_SCRIPTS = Path(__file__).resolve().parents[3] / "audience-ad-testing-lab" / "scripts"
if str(SIBLING_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SIBLING_SCRIPTS))

from audience_lab.audience_research import (
    AudienceResearchValidationError,
    RESEARCH_BRIEF_SCHEMA_VERSION,
    SAVED_PANEL_SCHEMA_VERSION,
    require_valid_audience_research_pair,
)
from audience_lab.audience_research_v3 import (
    RESEARCH_BRIEF_V3,
    SAVED_PANEL_V3,
    _v2_projection,
    _validate_v3_brief,
    _validate_v3_panel,
    validate_composition_plan,
    validate_population_frame,
    validate_validity_profile,
)

from .common import (
    ContractError,
    canonical_json_bytes,
    create_new_directory,
    require_array,
    require_identifier,
    require_object,
    require_string,
    require_string_array,
    require_timestamp,
    require_url,
    sanitize_excerpt,
    sha256_json,
    write_new_bytes,
)
from .evidence import validate_evidence_ledger, validate_finding_support
from .review import (
    validate_panel_review_manifest,
)
from .source_scoring import _CANDIDATE_KEYS, SCORED_SCHEMA_VERSION, score_source_candidates
from .synthesis import validate_synthesis_matrix
from .workflow_state import (
    require_approved_scope,
    validate_workflow_state,
    workflow_state_sha256,
)


REPORT_INPUT_SCHEMA_VERSION = "audience-research-report-inputs-v1"
REPORT_MANIFEST_SCHEMA_VERSION = "audience-research-report-manifest-v2"
SOURCE_INVENTORY_SCHEMA_VERSION = "audience-source-inventory-v1"
VERBATIM_INVENTORY_SCHEMA_VERSION = "audience-verbatim-inventory-v1"

_REPORT_INPUT_KEYS = {
    "schema_version", "panel_id", "panel_version", "workflow_state_sha256",
    "frame_sha256", "evidence_ledger_sha256", "finding_support_sha256",
    "synthesis_matrix_sha256", "scored_sources_sha256", "composition_sha256",
    "validity_sha256", "source_inventory_sha256", "verbatim_inventory_sha256",
}
_DOCUMENT_KEYS = {
    "workflow_state", "brief", "panel", "plan", "scored_sources",
    "evidence_ledger", "finding_support", "synthesis_matrix",
    "source_inventory", "verbatim_inventory",
}
_V3_DOCUMENT_KEYS = {
    "population_frame", "composition_plan", "validity_profile",
}
_SOURCE_INVENTORY_KEYS = {"schema_version", "sources"}
_SOURCE_KEYS = {
    "source_id", "provenance_label", "lane", "decision", "source_url",
    "evidence_item_ids",
}
_VERBATIM_INVENTORY_KEYS = {"schema_version", "excerpts"}
_EXCERPT_KEYS = {
    "evidence_item_id", "source_id", "source_url", "finding_ids",
    "text_fidelity", "excerpt",
}
_SCORED_TOP_KEYS = {"schema_version", "plan_id", "created_at", "candidates", "summary"}
_SCORED_DERIVED_KEYS = {"score", "tier", "decision", "decision_reasons"}
_VALIDITY_STATEMENT = {
    "population_claim": "not_available",
    "reason": "No population frame or composition document is available in Release A.",
}
_SOURCE_LABEL_RE = re.compile(r"^Source [1-9][0-9]*$")


def _require_digest(value: Any, path: str, *, nullable: bool = False) -> str | None:
    if value is None and nullable:
        return None
    if value is None:
        raise ContractError(f"{path} must be a lowercase SHA-256 digest")
    digest = require_string(value, path)
    if len(digest) != 64 or any(character not in "0123456789abcdef" for character in digest):
        raise ContractError(f"{path} must be a lowercase SHA-256 digest")
    return digest


def validate_report_inputs(payload: object) -> dict[str, object]:
    """Validate Release A bindings or v3 usable/no-frame population bindings."""

    inputs = require_object(payload, _REPORT_INPUT_KEYS, "$")
    if inputs["schema_version"] != REPORT_INPUT_SCHEMA_VERSION:
        raise ContractError(
            f"$.schema_version must equal {REPORT_INPUT_SCHEMA_VERSION}"
        )
    v3_requested = inputs["composition_sha256"] is not None
    if inputs["frame_sha256"] is not None and not v3_requested:
        raise ContractError(
            "a non-null frame_sha256 requires a v3 composition_sha256"
        )
    result: dict[str, object] = {
        "schema_version": inputs["schema_version"],
        "panel_id": require_identifier(inputs["panel_id"], "$.panel_id"),
        "panel_version": require_string(inputs["panel_version"], "$.panel_version"),
    }
    for key in sorted(_REPORT_INPUT_KEYS - {"schema_version", "panel_id", "panel_version"}):
        result[key] = _require_digest(
            inputs[key], f"$.{key}", nullable=key in {"frame_sha256", "composition_sha256"}
        )
    return result


def _validate_research_pair(
    brief_payload: object,
    panel_payload: object,
) -> tuple[
    dict[str, object],
    dict[str, object],
    dict[str, object],
    dict[str, object],
]:
    """Dispatch exact v2/v3 schemas and return canonical binding projections."""

    brief_schema = (
        brief_payload.get("schema_version")
        if isinstance(brief_payload, Mapping)
        else None
    )
    panel_schema = (
        panel_payload.get("schema_version")
        if isinstance(panel_payload, Mapping)
        else None
    )
    try:
        if (
            brief_schema == RESEARCH_BRIEF_SCHEMA_VERSION
            and panel_schema == SAVED_PANEL_SCHEMA_VERSION
        ):
            require_valid_audience_research_pair(brief_payload, panel_payload)
            brief = dict(brief_payload)
            panel = dict(panel_payload)
            return brief, panel, brief, panel
        if brief_schema == RESEARCH_BRIEF_V3 and panel_schema == SAVED_PANEL_V3:
            brief = _validate_v3_brief(brief_payload)
            panel = _validate_v3_panel(panel_payload)
            binding_brief = _v2_projection(brief, brief=True)
            binding_panel = _v2_projection(panel, brief=False)
            require_valid_audience_research_pair(binding_brief, binding_panel)
            return brief, panel, binding_brief, binding_panel
    except (AudienceResearchValidationError, ValueError) as exc:
        raise ContractError(str(exc)) from exc
    raise ContractError(
        "brief and panel must use one matching canonical v2 or v3 schema pair"
    )


def _validate_scored_sources(payload: object) -> dict[str, object]:
    """Re-score canonical candidates to reject edited or unknown scored output."""

    scored = require_object(payload, _SCORED_TOP_KEYS, "$.scored_sources")
    if scored["schema_version"] != SCORED_SCHEMA_VERSION:
        raise ContractError(f"$.scored_sources.schema_version must equal {SCORED_SCHEMA_VERSION}")
    candidates = require_array(scored["candidates"], "$.scored_sources.candidates", nonempty=True)
    raw_candidates: list[dict[str, object]] = []
    for index, candidate in enumerate(candidates):
        if not isinstance(candidate, Mapping):
            raise ContractError(f"$.scored_sources.candidates[{index}] must be an object")
        unknown = sorted(set(candidate) - (_CANDIDATE_KEYS | _SCORED_DERIVED_KEYS))
        if unknown:
            raise ContractError(f"$.scored_sources.candidates[{index}] has unknown fields: {', '.join(unknown)}")
        missing = sorted(_SCORED_DERIVED_KEYS - set(candidate))
        if missing:
            raise ContractError(f"$.scored_sources.candidates[{index}] is missing fields: {', '.join(missing)}")
        raw_candidates.append({key: value for key, value in candidate.items() if key not in _SCORED_DERIVED_KEYS})
    expected = score_source_candidates({
        "schema_version": "audience-source-candidates-v1",
        "plan_id": scored["plan_id"],
        "created_at": scored["created_at"],
        "candidates": raw_candidates,
    })
    if canonical_json_bytes(expected) != canonical_json_bytes(scored):
        raise ContractError("$.scored_sources must equal the deterministic source scoring result")
    return dict(scored)


def build_source_inventory(
    *,
    scored_sources: dict[str, object],
    evidence_ledger: dict[str, object],
) -> dict[str, object]:
    """Create neutral, spreadsheet-safe source rows from scored source evidence."""

    scored = _validate_scored_sources(scored_sources)
    ledger = validate_evidence_ledger(evidence_ledger)
    evidence_ids = {item["evidence_item_id"] for item in ledger["evidence_items"]}
    sources: list[dict[str, object]] = []
    for index, candidate in enumerate(sorted(scored["candidates"], key=lambda item: item["candidate_id"]), start=1):
        referenced = list(candidate["evidence_item_ids"])
        unresolved = sorted(set(referenced) - evidence_ids)
        if unresolved:
            raise ContractError(
                "$.scored_sources candidate evidence_item_ids do not resolve: "
                + ", ".join(unresolved)
            )
        for evidence_item_id in referenced:
            item = next(
                item for item in ledger["evidence_items"]
                if item["evidence_item_id"] == evidence_item_id
            )
            if (
                set(candidate["upstream_source_ids"]) != set(item["upstream_source_ids"])
                or candidate["source_url"] != item["source_url"]
            ):
                raise ContractError(
                    "$.scored_sources candidate evidence_item_ids must match the canonical upstream identity set and source URL"
                )
        sources.append({
            "source_id": candidate["candidate_id"],
            "provenance_label": f"Source {index}",
            "lane": candidate["lane"],
            "decision": candidate["decision"],
            "source_url": candidate["source_url"],
            "evidence_item_ids": sorted(referenced),
        })
    return {"schema_version": SOURCE_INVENTORY_SCHEMA_VERSION, "sources": sources}


def build_verbatim_inventory(
    *,
    evidence_ledger: dict[str, object],
    finding_support: dict[str, object],
) -> dict[str, object]:
    """Create one excerpt row for every supported evidence item with full bindings."""

    ledger = validate_evidence_ledger(evidence_ledger)
    support = validate_finding_support(finding_support, ledger)
    item_by_id = {item["evidence_item_id"]: item for item in ledger["evidence_items"]}
    import_ids = {item["import_id"] for item in ledger["imports"]}
    findings_by_item: dict[str, set[str]] = defaultdict(set)
    for finding in support["findings"]:
        for evidence_item_id in finding["evidence_item_ids"]:
            findings_by_item[evidence_item_id].add(finding["finding_id"])
    excerpts: list[dict[str, object]] = []
    for evidence_item_id in sorted(findings_by_item):
        item = item_by_id.get(evidence_item_id)
        if item is None or item["import_id"] not in import_ids:
            raise ContractError(
                f"$.finding_support excerpt {evidence_item_id} cannot resolve to a source"
            )
        finding_ids = sorted(findings_by_item[evidence_item_id])
        if not finding_ids:
            raise ContractError(
                f"$.finding_support excerpt {evidence_item_id} cannot resolve to a finding"
            )
        excerpts.append({
            "evidence_item_id": evidence_item_id,
            "source_id": item["import_id"],
            "source_url": item["source_url"],
            "finding_ids": finding_ids,
            "text_fidelity": item["text_fidelity"],
            "excerpt": sanitize_excerpt(item["content_summary"]),
        })
    return {"schema_version": VERBATIM_INVENTORY_SCHEMA_VERSION, "excerpts": excerpts}


def _validate_source_inventory(payload: object, ledger: dict[str, object]) -> dict[str, object]:
    inventory = require_object(payload, _SOURCE_INVENTORY_KEYS, "$.source_inventory")
    if inventory["schema_version"] != SOURCE_INVENTORY_SCHEMA_VERSION:
        raise ContractError(f"$.source_inventory.schema_version must equal {SOURCE_INVENTORY_SCHEMA_VERSION}")
    evidence_ids = {item["evidence_item_id"] for item in ledger["evidence_items"]}
    seen: set[str] = set()
    previous_label = 0
    sources: list[dict[str, object]] = []
    for index, raw in enumerate(require_array(inventory["sources"], "$.source_inventory.sources", nonempty=True)):
        path = f"$.source_inventory.sources[{index}]"
        source = require_object(raw, _SOURCE_KEYS, path)
        source_id = require_identifier(source["source_id"], f"{path}.source_id")
        if source_id in seen:
            raise ContractError(f"{path}.source_id is duplicated")
        seen.add(source_id)
        label = require_string(source["provenance_label"], f"{path}.provenance_label")
        if not _SOURCE_LABEL_RE.fullmatch(label):
            raise ContractError(f"{path}.provenance_label must be a neutral Source N label")
        number = int(label.removeprefix("Source "))
        if number <= previous_label:
            raise ContractError(f"{path}.provenance_label must be in ascending order")
        previous_label = number
        require_string(source["lane"], f"{path}.lane")
        require_string(source["decision"], f"{path}.decision")
        require_url(source["source_url"], f"{path}.source_url")
        referenced = require_string_array(source["evidence_item_ids"], f"{path}.evidence_item_ids", nonempty=True)
        unresolved = sorted(set(referenced) - evidence_ids)
        if unresolved:
            raise ContractError(f"{path}.evidence_item_ids do not resolve: {', '.join(unresolved)}")
        sources.append(dict(source))
    return {"schema_version": inventory["schema_version"], "sources": sources}


def _validate_verbatim_inventory(
    payload: object,
    ledger: dict[str, object],
    support: dict[str, object],
) -> dict[str, object]:
    inventory = require_object(payload, _VERBATIM_INVENTORY_KEYS, "$.verbatim_inventory")
    if inventory["schema_version"] != VERBATIM_INVENTORY_SCHEMA_VERSION:
        raise ContractError(f"$.verbatim_inventory.schema_version must equal {VERBATIM_INVENTORY_SCHEMA_VERSION}")
    item_by_id = {item["evidence_item_id"]: item for item in ledger["evidence_items"]}
    import_ids = {item["import_id"] for item in ledger["imports"]}
    findings_by_item: dict[str, set[str]] = defaultdict(set)
    for finding in support["findings"]:
        for evidence_item_id in finding["evidence_item_ids"]:
            findings_by_item[evidence_item_id].add(finding["finding_id"])
    seen: set[str] = set()
    excerpts: list[dict[str, object]] = []
    for index, raw in enumerate(require_array(inventory["excerpts"], "$.verbatim_inventory.excerpts")):
        path = f"$.verbatim_inventory.excerpts[{index}]"
        excerpt = require_object(raw, _EXCERPT_KEYS, path)
        evidence_item_id = require_identifier(excerpt["evidence_item_id"], f"{path}.evidence_item_id")
        if evidence_item_id in seen:
            raise ContractError(f"{path}.evidence_item_id is duplicated")
        seen.add(evidence_item_id)
        item = item_by_id.get(evidence_item_id)
        if item is None:
            raise ContractError(f"{path}.evidence_item_id cannot resolve to a source")
        source_id = require_identifier(excerpt["source_id"], f"{path}.source_id")
        if source_id not in import_ids or source_id != item["import_id"]:
            raise ContractError(f"{path}.source_id cannot resolve to the evidence source")
        source_url = require_url(excerpt["source_url"], f"{path}.source_url", allow_empty=True)
        if source_url != item["source_url"]:
            raise ContractError(f"{path}.source_url must match the evidence source")
        finding_ids = require_string_array(excerpt["finding_ids"], f"{path}.finding_ids", nonempty=True)
        if set(finding_ids) != findings_by_item.get(evidence_item_id, set()):
            raise ContractError(f"{path}.finding_ids cannot resolve to the exact finding support")
        if require_string(excerpt["text_fidelity"], f"{path}.text_fidelity") != item["text_fidelity"]:
            raise ContractError(f"{path}.text_fidelity must match the evidence item")
        if require_string(excerpt["excerpt"], f"{path}.excerpt", allow_empty=True) != sanitize_excerpt(item["content_summary"]):
            raise ContractError(f"{path}.excerpt must match the sanitized evidence summary")
        excerpts.append(dict(excerpt))
    if seen != set(findings_by_item):
        raise ContractError("$.verbatim_inventory.excerpts must cover every finding-supported evidence item")
    return {"schema_version": inventory["schema_version"], "excerpts": excerpts}


def _validated_documents(documents: dict[str, dict[str, object]], report_inputs: dict[str, object]) -> dict[str, dict[str, object]]:
    if not isinstance(documents, Mapping):
        raise ContractError("documents must be an object")
    v3_mode = report_inputs["composition_sha256"] is not None
    document_keys = _DOCUMENT_KEYS | (_V3_DOCUMENT_KEYS if v3_mode else set())
    missing = sorted(document_keys - set(documents))
    unknown = sorted(set(documents) - document_keys)
    if missing:
        raise ContractError("missing canonical documents: " + ", ".join(missing))
    if unknown:
        raise ContractError("documents has unknown fields: " + ", ".join(unknown))
    if any(not isinstance(value, Mapping) for value in documents.values()):
        raise ContractError("every canonical document must be an object")
    workflow_state = validate_workflow_state(documents["workflow_state"])
    brief, panel, binding_brief, binding_panel = _validate_research_pair(
        documents["brief"],
        documents["panel"],
    )
    brief_sha256 = sha256_json(binding_brief).removeprefix("sha256:")
    panel_sha256 = sha256_json(binding_panel).removeprefix("sha256:")
    if workflow_state["bindings"]["brief_sha256"] != brief_sha256:
        raise ContractError("$.workflow_state.bindings.brief_sha256 must match the exact canonical brief")
    if workflow_state["bindings"]["panel_sha256"] != panel_sha256:
        raise ContractError("$.workflow_state.bindings.panel_sha256 must match the exact canonical panel")
    if workflow_state["state"] == "draft":
        raise ContractError("draft workflow state cannot render an audience research report")
    if workflow_state["panel_id"] != report_inputs["panel_id"] or panel["panel_id"] != report_inputs["panel_id"]:
        raise ContractError("report inputs, workflow state, and panel must bind the same panel_id")
    if workflow_state["panel_version"] != report_inputs["panel_version"] or panel["version"] != report_inputs["panel_version"]:
        raise ContractError("report inputs, workflow state, and panel must bind the same panel_version")
    ledger = validate_evidence_ledger(documents["evidence_ledger"])
    support = validate_finding_support(documents["finding_support"], ledger)
    synthesis = validate_synthesis_matrix(documents["synthesis_matrix"], ledger, support)
    if workflow_state["state"] == "approved":
        require_approved_scope(
            workflow_state,
            scope="evidence_synthesis",
            target_sha256=sha256_json(synthesis).removeprefix("sha256:"),
        )
    scored = _validate_scored_sources(documents["scored_sources"])
    plan = documents["plan"]
    plan_id = require_identifier(plan.get("plan_id"), "$.plan.plan_id")
    for name, value in (("ledger", ledger), ("synthesis", synthesis), ("scored_sources", scored)):
        if value["plan_id"] != plan_id:
            raise ContractError(f"$.{name}.plan_id must match $.plan.plan_id")
    source_inventory = _validate_source_inventory(documents["source_inventory"], ledger)
    verbatim_inventory = _validate_verbatim_inventory(documents["verbatim_inventory"], ledger, support)
    if canonical_json_bytes(source_inventory) != canonical_json_bytes(
        build_source_inventory(scored_sources=scored, evidence_ledger=ledger)
    ):
        raise ContractError("$.source_inventory must equal the deterministic source inventory")
    if canonical_json_bytes(verbatim_inventory) != canonical_json_bytes(
        build_verbatim_inventory(evidence_ledger=ledger, finding_support=support)
    ):
        raise ContractError("$.verbatim_inventory must equal the deterministic verbatim inventory")
    population_documents: dict[str, dict[str, object]] = {}
    if v3_mode:
        try:
            frame_result = validate_population_frame(
                documents["population_frame"]
            )
            usable_frame = frame_result["eligibility"] in {
                "eligible_tier_2",
                "eligible_tier_3",
            }
            composition = validate_composition_plan(
                documents["composition_plan"],
                frame=frame_result,
            )
            validity = validate_validity_profile(documents["validity_profile"])
        except ValueError as exc:
            raise ContractError(str(exc)) from exc
        if validity["binding_state"] != "panel_final":
            raise ContractError(
                "$.validity_profile.binding_state must be panel_final for a v3 report"
            )
        frame_result_digest = sha256_json(frame_result)
        frame_digest = frame_result_digest if usable_frame else None
        composition_digest = sha256_json(composition)
        validity_digest = sha256_json(validity)
        if validity["panel_id"] != panel["panel_id"]:
            raise ContractError(
                "$.validity_profile.panel_id must match the canonical panel"
            )
        exact_validity_bindings = {
            "brief_sha256": sha256_json(binding_brief),
            "panel_sha256": sha256_json(binding_panel),
            "frame_result_sha256": frame_result_digest,
            "frame_sha256": frame_digest,
            "composition_sha256": composition_digest,
        }
        for key, expected_binding in exact_validity_bindings.items():
            if validity["source_bindings"][key] != expected_binding:
                raise ContractError(
                    f"$.validity_profile.source_bindings.{key} must match "
                    "the exact canonical brief, panel, population-frame "
                    "result/usable frame, and composition plan"
                )
        expected_frame_input = (
            None
            if frame_digest is None
            else frame_digest.removeprefix("sha256:")
        )
        if report_inputs["frame_sha256"] != expected_frame_input:
            raise ContractError(
                "$.frame_sha256 does not match the exact canonical population frame"
            )
        for key, digest, label in (
            ("composition_sha256", composition_digest, "composition plan"),
            ("validity_sha256", validity_digest, "validity profile"),
        ):
            if report_inputs[key] != digest.removeprefix("sha256:"):
                raise ContractError(
                    f"$.{key} does not match the exact canonical {label}"
                )
        if brief["schema_version"] == RESEARCH_BRIEF_V3:
            for document, path in ((brief, "$.brief"), (panel, "$.panel")):
                if (
                    document["population_frame_result_sha256"]
                    != frame_result_digest
                ):
                    raise ContractError(
                        f"{path}.population_frame_result_sha256 must match "
                        "the canonical population-frame result"
                    )
                if document["population_frame_sha256"] != frame_digest:
                    raise ContractError(
                        f"{path}.population_frame_sha256 must match the "
                        "canonical usable population frame"
                    )
            if panel["composition_plan_sha256"] != composition_digest:
                raise ContractError(
                    "$.panel.composition_plan_sha256 must match the "
                    "canonical composition plan"
                )
            if panel["validity_profile_sha256"] != validity_digest:
                raise ContractError(
                    "$.panel.validity_profile_sha256 must match the "
                    "canonical validity profile"
                )
        population_documents = {
            "population_frame": frame_result,
            "composition_plan": composition,
            "validity_profile": validity,
        }
    expected_hashes = {
        "workflow_state_sha256": workflow_state_sha256(workflow_state),
        "evidence_ledger_sha256": sha256_json(ledger).removeprefix("sha256:"),
        "finding_support_sha256": sha256_json(support).removeprefix("sha256:"),
        "synthesis_matrix_sha256": sha256_json(synthesis).removeprefix("sha256:"),
        "scored_sources_sha256": sha256_json(scored).removeprefix("sha256:"),
        "source_inventory_sha256": sha256_json(source_inventory).removeprefix("sha256:"),
        "verbatim_inventory_sha256": sha256_json(verbatim_inventory).removeprefix("sha256:"),
        "validity_sha256": (
            sha256_json(population_documents["validity_profile"]).removeprefix("sha256:")
            if v3_mode
            else sha256_json(_VALIDITY_STATEMENT).removeprefix("sha256:")
        ),
    }
    for key, expected in expected_hashes.items():
        if report_inputs[key] != expected:
            raise ContractError(f"$.{key} does not match the exact canonical document")
    return {
        "workflow_state": workflow_state,
        "brief": brief,
        "panel": panel,
        "plan": dict(plan),
        "scored_sources": scored,
        "evidence_ledger": ledger,
        "finding_support": support,
        "synthesis_matrix": synthesis,
        "source_inventory": source_inventory,
        "verbatim_inventory": verbatim_inventory,
        **population_documents,
    }


def _safe(value: object) -> str:
    return escape(str(value), quote=True)


def _citation(url: str) -> str:
    if not url:
        return "No public URL recorded"
    return f'<a href="{_safe(url)}" rel="noopener noreferrer">Citation</a>'


def _table(headers: list[str], rows: list[list[str]]) -> str:
    head = "".join(f"<th>{_safe(header)}</th>" for header in headers)
    body = "".join("<tr>" + "".join(f"<td>{cell}</td>" for cell in row) + "</tr>" for row in rows)
    return f"<table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>"


def _render_html(report_inputs: dict[str, object], documents: dict[str, dict[str, object]], generated_at: str) -> str:
    template_path = Path(__file__).resolve().parents[2] / "assets" / "audience-research-report-template.html"
    template = template_path.read_text(encoding="utf-8")
    if set(re.findall(r"\{\{[A-Z_]+\}\}", template)) != {"{{TITLE}}", "{{BODY}}"}:
        raise ContractError("research report template must contain only TITLE and BODY placeholders")
    state = documents["workflow_state"]["state"]
    status = str(state).replace("_", " ").upper()
    brief = documents["brief"]
    panel = documents["panel"]
    ledger = documents["evidence_ledger"]
    matrix = documents["synthesis_matrix"]
    sources = documents["source_inventory"]["sources"]
    excerpts = documents["verbatim_inventory"]["excerpts"]
    coverage = brief["coverage"]
    findings = [finding for question in matrix["questions"] for finding in question["findings"]]
    source_rows = [
        [_safe(source["provenance_label"]), _safe(source["lane"]), _safe(source["decision"]), _citation(str(source["source_url"]))]
        for source in sources
    ]
    def role_items(finding: Mapping[str, object], key: str) -> str:
        values = finding[key]
        return ", ".join(values) if values else "None"

    finding_rows = [
        [
            _safe(finding["finding_id"]),
            _safe(finding["statement"]),
            _safe(f"{finding['confidence']}: {finding['confidence_reason']}"),
            _safe(finding["integration_state"]),
            _safe(finding["inference_boundary"]),
            _safe(", ".join(
                f"{label.replace('_', ' ')}: {str(finding[label]).replace('_', ' ')}"
                for label in ("methodological_limitations", "relevance", "coherence", "adequacy")
            )),
            _safe(role_items(finding, "supporting_item_ids")),
            _safe(role_items(finding, "qualifying_item_ids")),
            _safe(role_items(finding, "contradicting_item_ids")),
        ]
        for finding in findings
    ]
    coverage_rows = [[_safe(name.replace("_", " ").title()), _safe(level)] for name, level in sorted(coverage.items())]
    excerpt_rows = [
        [_safe(excerpt["evidence_item_id"]), _safe(", ".join(excerpt["finding_ids"])), _safe(excerpt["text_fidelity"]), _safe(excerpt["excerpt"]), _citation(str(excerpt["source_url"]))]
        for excerpt in excerpts
    ]
    decisions = Counter(source["decision"] for source in sources)
    gaps = "".join(f"<li>{_safe(gap['gap'])}: {_safe(gap['impact_on_panel'])}</li>" for gap in brief["evidence_gaps"]) or "<li>None recorded.</li>"
    contradictions = [finding for finding in findings if finding["contradicting_item_ids"]]
    contradiction_rows = [
        [
            _safe(finding["finding_id"]),
            _safe(role_items(finding, "supporting_item_ids")),
            _safe(role_items(finding, "qualifying_item_ids")),
            _safe(role_items(finding, "contradicting_item_ids")),
            _safe(finding["inference_boundary"]),
        ]
        for finding in contradictions
    ] or [["No contradicting findings", "None", "None", "None", "No additional boundary recorded"]]
    approvals = documents["workflow_state"]["approvals"]
    approval_rows = [[_safe(row["scope"]), _safe(row["status"]), _safe(row["approved_at"] or "Not recorded")] for row in approvals] or [["No scoped approvals", "Not recorded", "Not recorded"]]
    if "population_frame" in documents:
        frame = documents["population_frame"]
        composition = documents["composition_plan"]
        validity = documents["validity_profile"]
        unit_rows = [
            [
                _safe(unit["partition_id"]),
                _safe(unit["unit"]),
                _safe(unit["denominator"]),
                _safe("Yes" if unit["exact"] else "No"),
            ]
            for unit in frame["units"]
        ]
        cell_rows = [
            [
                _safe(cell["cell_id"]),
                _safe(cell["partition_id"]),
                _safe(", ".join(
                    f"{name}={value}"
                    for name, value in sorted(cell["dimension_values"].items())
                )),
                _safe(str(cell["status"]).title()),
                _safe(
                    "Not available"
                    if cell["structural_weight"] is None
                    else f"{float(cell['structural_weight']):.1%}"
                ),
                _safe(cell["weight_semantic"] or "not_available"),
                _safe(
                    "suppressed"
                    if cell["suppressed"]
                    else (
                        "not available"
                        if cell["uncertainty"]["lower"] is None
                        else (
                            f"{float(cell['uncertainty']['lower']):.1%}–"
                            f"{float(cell['uncertainty']['upper']):.1%}"
                        )
                    )
                ),
            ]
            for cell in frame["cells"]
        ]
        missing_rows = [
            [
                _safe(joint["partition_id"]),
                _safe(" × ".join(joint["dimensions"])),
                _safe(joint["missing_reason"]),
            ]
            for joint in frame["joints"]
            if joint["missing_reason"] is not None
        ] or [["None", "None", "No missing critical joints recorded."]]
        modeled_rows = [
            [
                _safe(row["partition_id"]),
                _safe(row["dimension"]),
                _safe(f"{float(row['share']):.1%}"),
                _safe(str(row["status"]).title()),
            ]
            for row in frame["modeled_weight_by_dimension"]
        ]
        axis_rows = [
            [
                _safe(axis.replace("_", " ").title()),
                _safe(str(value["status"]).replace("_", " ").title()),
                _safe(
                    "Not available"
                    if value["coverage"] is None
                    else f"{float(value['coverage']):.1%}"
                ),
                _safe("; ".join(value["limitations"]) or "None recorded"),
            ]
            for axis, value in sorted(validity["axes"].items())
        ]
        source_coverage = [
            _safe(binding["coverage_notes"]) for binding in frame["source_bindings"]
        ]
        gaps_text = "; ".join(frame["coverage_assessment"]["known_gaps"]) or "None recorded"
        downgrade = frame["downgrade_reason"] or "None"
        selection = composition["frame_binding"]["selection"]
        selection_text = (
            "Not available (Tier 1 evidence route)"
            if selection is None
            else (
                f"{selection['partition_id']} / {selection['relationship']} / "
                + " × ".join(selection["dimensions"])
            )
        )
        profile_rows = [
            [
                _safe(profile["profile_id"]),
                _safe(profile["structural_group_id"]),
                _safe(" + ".join(profile["overlay_ids"])),
                _safe(f"{float(profile['effective_profile_allocation']):.1%}"),
                _safe(profile["effective_weight_semantic"]),
            ]
            for profile in composition["profiles"]
        ]
        unsupported_rows = [
            [
                _safe(row["structural_group_id"]),
                _safe(" + ".join(row["overlay_ids"])),
                _safe(row["reason_code"]),
                _safe(row["reason"]),
            ]
            for row in composition["unsupported_combinations"]
        ] or [["None", "None", "None", "No unsupported combinations recorded."]]
        population_section = (
            "<section><h2>Composition and population validity</h2>"
            f"<p><strong>Structural universe:</strong> {_safe(frame['target_universe'])}</p>"
            f"<p><strong>Proxy/authorized-cohort boundary:</strong> {_safe(frame['claim_boundary'])}</p>"
            + _table(
                ["Partition", "Unit", "Denominator", "Exact"],
                unit_rows,
            )
            + "<h3>Observed, modeled, and missing cells</h3>"
            + _table(
                [
                    "Cell", "Partition", "Dimensions", "Status", "Weight",
                    "Weight semantics", "Uncertainty / suppression",
                ],
                cell_rows,
            )
            + "<h3>Modeled effective weight by dimension</h3>"
            + _table(["Partition", "Dimension", "Modeled share", "Status"], modeled_rows)
            + "<h3>Missing joints</h3>"
            + _table(["Partition", "Joint", "Reason"], missing_rows)
            + f"<p><strong>Coverage:</strong> {_safe(frame['coverage_assessment']['coverage_statement'])}</p>"
            + f"<p><strong>Known gaps:</strong> {_safe(gaps_text)}</p>"
            + f"<p><strong>Transformation loss and source coverage:</strong> {_safe('; '.join(source_coverage) or 'None recorded')}</p>"
            + f"<p><strong>Downgrade reasons:</strong> {_safe(downgrade)}</p>"
            + "<h3>Partition-aware composition</h3>"
            + (
                "<p><strong>Requested "
                f"{_safe(str(composition['requested_tier']).replace('_', ' ').title())}; "
                "achieved "
                f"{_safe(str(composition['achieved_tier']).replace('_', ' ').title())}"
                "</strong></p>"
            )
            + f"<p><strong>Evidence basis:</strong> {_safe(str(composition['evidence_basis']).replace('_', ' ').title())}</p>"
            + f"<p><strong>Selected structural collection:</strong> {_safe(selection_text)}</p>"
            + _table(
                ["Profile", "Structural group", "Overlay set", "Effective allocation", "Semantic"],
                profile_rows,
            )
            + "<h4>Unsupported combinations</h4>"
            + _table(
                ["Structural group", "Overlay set", "Reason code", "Reason"],
                unsupported_rows,
            )
            + f"<p><strong>Composition modeled-cell share:</strong> {_safe(f'{float(composition['modeled_cell_share']):.1%}')}</p>"
            + "<h3>Separate validity axes</h3>"
            + _table(["Axis", "Status", "Coverage", "Limitations"], axis_rows)
            + "</section>"
        )
    else:
        population_section = (
            "<section><h2>Composition and population validity</h2><p><strong>"
            "Population validity is unavailable.</strong> Release A has no "
            "population frame or composition document. Existing planning "
            "weights remain directional and must not be treated as population "
            "prevalence.</p></section>"
        )
    body = "\n".join([
        f'<header class="status status-{_safe(state)}"><p>RESEARCH REPORT</p><h1>{_safe(panel["panel_name"])}</h1><strong>{_safe(status)}</strong><p>Generated { _safe(generated_at) }</p></header>',
        "<section><h2>What this panel can and cannot support</h2><p>This panel supports directional creative hypothesis stress testing and prioritization within the documented audience scope. It cannot establish population representativeness, market prevalence, individual targeting, or outcome prediction.</p></section>",
        f"<section><h2>Research question and scope</h2><p>{_safe(' '.join(brief['research_questions']))}</p><p>{_safe(brief['target_audience']['audience'])} — {_safe(brief['target_audience']['geography'])}; {_safe(brief['target_audience']['buying_context'])}.</p></section>",
        f"<section><h2>Evidence-base summary</h2><p>{len(ledger['evidence_items'])} item-level evidence record(s); {len(sources)} scored source(s); " + ", ".join(f"{_safe(key)}: {_safe(value)}" for key, value in sorted(decisions.items())) + ".</p></section>",
        "<section><h2>Source inventory</h2>" + _table(["Label", "Evidence lane", "Decision", "Citation"], source_rows) + "</section>",
        "<section><h2>Finding-support matrix</h2>" + _table(["Finding", "Statement", "Confidence and reason", "Integration", "Inference boundary", "Methodological concerns", "Supports", "Qualifies", "Contradicts"], finding_rows) + "</section>",
        "<section><h2>Contradictions, gaps, and confidence</h2><p>Contradicting findings: " + _safe(", ".join(item["finding_id"] for item in contradictions) or "None recorded") + ".</p>" + _table(["Finding", "Supports", "Qualifies", "Contradicts", "Inference boundary"], contradiction_rows) + "<ul>" + gaps + "</ul></section>",
        "<section><h2>Segment sufficiency and coverage</h2>" + _table(["Coverage area", "Recorded sufficiency"], coverage_rows) + "</section>",
        f"<section><h2>Panel construction rationale</h2><p>{len(panel['segments'])} audience group(s), {len(panel['persona_archetypes'])} buyer mindset(s), {len(panel['context_strata'])} buying situation(s), and {len(panel['grounded_context_profiles'])} reusable profile(s) are retained from the approved panel.</p></section>",
        population_section,
        "<section><h2>Verbatim/excerpt inventory</h2>" + _table(["Evidence item", "Finding bindings", "Text fidelity", "Excerpt", "Citation"], excerpt_rows) + "</section>",
        "<section><h2>Approval and audit status</h2>" + _table(["Scope", "Status", "Recorded at"], approval_rows) + f"<p>Workflow state: {_safe(status)}. Construction audit: not yet recorded in this Release A report surface.</p></section>",
    ])
    return template.replace("{{TITLE}}", _safe(f"{panel['panel_name']} research report")).replace("{{BODY}}", body)


def _manifest_entry(path: str, contents: bytes) -> dict[str, object]:
    return {"path": path, "sha256": hashlib.sha256(contents).hexdigest(), "bytes": len(contents)}


def _release_a_report_inputs(documents: Mapping[str, Mapping[str, object]]) -> dict[str, object]:
    """Compile the Release A null-frame bindings from the supplied documents."""

    validity_hash = sha256_json(_VALIDITY_STATEMENT).removeprefix("sha256:")
    return {
        "schema_version": REPORT_INPUT_SCHEMA_VERSION,
        "panel_id": documents["panel"]["panel_id"],
        "panel_version": documents["panel"]["version"],
        "workflow_state_sha256": workflow_state_sha256(documents["workflow_state"]),
        "frame_sha256": None,
        "evidence_ledger_sha256": sha256_json(documents["evidence_ledger"]).removeprefix("sha256:"),
        "finding_support_sha256": sha256_json(documents["finding_support"]).removeprefix("sha256:"),
        "synthesis_matrix_sha256": sha256_json(documents["synthesis_matrix"]).removeprefix("sha256:"),
        "scored_sources_sha256": sha256_json(documents["scored_sources"]).removeprefix("sha256:"),
        "composition_sha256": None,
        "validity_sha256": validity_hash,
        "source_inventory_sha256": sha256_json(documents["source_inventory"]).removeprefix("sha256:"),
        "verbatim_inventory_sha256": sha256_json(documents["verbatim_inventory"]).removeprefix("sha256:"),
    }


def render_research_report(
    *,
    report_inputs: dict[str, object],
    documents: dict[str, dict[str, object]],
    generated_at: str,
    output_dir: Path,
    panel_review_manifest: dict[str, object] | None = None,
    panel_review_summary: bytes | None = None,
    panel_review_html: bytes | None = None,
) -> dict[str, object]:
    """Validate, render, and write one no-clobber Release A report directory."""

    validated_inputs = validate_report_inputs(report_inputs)
    require_timestamp(generated_at, "generated_at")
    validated_documents = _validated_documents(documents, validated_inputs)
    supplied_review_parts = (
        panel_review_manifest,
        panel_review_summary,
        panel_review_html,
    )
    if not all(value is not None for value in supplied_review_parts):
        raise ContractError(
            "panel review manifest, Markdown summary, and HTML review are required together"
        )
    panel_review_manifest = validate_panel_review_manifest(
        panel_review_manifest,
        panel=validated_documents["panel"],
        summary_bytes=panel_review_summary,
        html_bytes=panel_review_html,
    )
    html = _render_html(validated_inputs, validated_documents, generated_at)
    html_bytes = html.encode("utf-8")
    source_bytes = canonical_json_bytes(validated_documents["source_inventory"])
    verbatim_bytes = canonical_json_bytes(validated_documents["verbatim_inventory"])
    input_bytes = {
        "brief.json": canonical_json_bytes(validated_documents["brief"]),
        "evidence-ledger.json": canonical_json_bytes(validated_documents["evidence_ledger"]),
        "finding-support.json": canonical_json_bytes(validated_documents["finding_support"]),
        "plan.json": canonical_json_bytes(validated_documents["plan"]),
        "report-inputs.json": canonical_json_bytes(validated_inputs),
        "saved-audience-panel.json": canonical_json_bytes(validated_documents["panel"]),
        "scored-sources.json": canonical_json_bytes(validated_documents["scored_sources"]),
        "source-inventory.json": source_bytes,
        "synthesis-matrix.json": canonical_json_bytes(validated_documents["synthesis_matrix"]),
        "verbatim-inventory.json": verbatim_bytes,
        "workflow-state.json": canonical_json_bytes(validated_documents["workflow_state"]),
    }
    input_bytes["panel-review-manifest.json"] = canonical_json_bytes(
        panel_review_manifest
    )
    if "population_frame" in validated_documents:
        input_bytes.update({
            "population-frame.json": canonical_json_bytes(
                validated_documents["population_frame"]
            ),
            "composition-plan.json": canonical_json_bytes(
                validated_documents["composition_plan"]
            ),
            "validity-profile.json": canonical_json_bytes(
                validated_documents["validity_profile"]
            ),
        })
    output_bytes = {
        "audience-research-report.html": html_bytes,
        "source-inventory.json": source_bytes,
        "verbatim-inventory.json": verbatim_bytes,
    }
    manifest = {
        "schema_version": REPORT_MANIFEST_SCHEMA_VERSION,
        "panel_id": validated_inputs["panel_id"],
        "panel_version": validated_inputs["panel_version"],
        "generated_at": generated_at,
        "report_inputs_sha256": sha256_json(validated_inputs).removeprefix("sha256:"),
        "inputs": [_manifest_entry(path, contents) for path, contents in sorted(input_bytes.items())],
        "outputs": [_manifest_entry(path, contents) for path, contents in sorted(output_bytes.items())],
    }
    manifest_bytes = canonical_json_bytes(manifest)
    directory = create_new_directory(output_dir, "audience research report output directory")
    for path, contents in output_bytes.items():
        write_new_bytes(directory / path, contents, f"audience research report {path}")
    write_new_bytes(directory / "audience-research-report-manifest.json", manifest_bytes, "audience research report manifest")
    return manifest
