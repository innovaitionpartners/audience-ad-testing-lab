"""Question-driven source planning with verified connector capabilities."""

from __future__ import annotations

from typing import Any, Mapping

from .capabilities import (
    capability_inventory_sha256,
    validate_capability_inventory,
    verified_capabilities,
)
from .common import (
    ContractError,
    require_array,
    require_enum,
    require_identifier,
    require_object,
    require_string,
    require_string_array,
    require_timestamp,
    require_url,
    sha256_json,
)


INTAKE_SCHEMA_VERSION = "audience-panel-research-intake-v1"
REGISTRY_SCHEMA_VERSION = "audience-source-registry-v1"
PLAN_SCHEMA_VERSION = "audience-source-plan-v1"
# Four months is a review trigger, not an expiry: recurring-source links and
# connector assumptions should be rechecked before planning from an older registry.
REGISTRY_REVIEW_AGE_DAYS = 120

_INTAKE_KEYS = {
    "schema_version", "research_id", "created_at", "workflow_route",
    "target_audience", "audience_type", "research_depth", "decision_to_support",
    "available_inputs", "requested_or_supplied_connectors",
    "current_language_evidence", "languages", "as_of", "existing_panel",
}
_TARGET_KEYS = {
    "audience", "category", "market", "geography", "buying_context", "exclusions",
}
_EXISTING_KEYS = {"panel_id", "version", "package_sha256"}
_REGISTRY_KEYS = {"schema_version", "updated_at", "source_families"}
_FAMILY_KEYS = {
    "source_family_id", "lane", "name", "publisher", "source_kind",
    "audience_types", "geographies", "cadence", "access_mode", "landing_url",
    "methodology_url", "usable_for", "discovery_queries", "selection_notes",
    "connector",
}
_AUDIENCE_TYPES = {
    "b2b", "consumer", "workforce", "technology", "small_business", "mixed",
}
_DEPTHS = {"quick_directional", "standard", "robust"}
_ROUTES = {
    "create_research_backed_panel", "import_authorized_audience",
    "refresh_existing_panel",
    "augment_existing_panel", "audit_existing_panel",
    "provisional_immediate_panel",
}
_INPUTS = {
    "user_research", "interviews_aggregate", "sales_themes", "support_themes",
    "owned_social_aggregate", "social_listening_export",
    "first_party_evidence_package", "performance_evidence_package",
    "last30days_json", "mapped_social_export",
}
_CONNECTOR_HINTS = {
    "sprout", "sprinklr", "brandwatch", "meltwater", "talkwalker", "pulsar",
    "authenticated_social_connector", "native_web_research",
    "last30days_json_import", "mapped_social_export", "licensed_export", "none",
}
_CURRENT_LANGUAGE = {"required", "useful", "not_applicable"}
_LANES = {"structural", "survey", "social_community", "first_party", "performance"}
_DEPTH_MINIMUMS = {
    "quick_directional": {"structural": 1, "survey": 1},
    "standard": {"structural": 2, "survey": 3},
    "robust": {"structural": 3, "survey": 5},
}
_LISTENING_CAPABILITIES = {
    "query_saved_listening_topics", "query_ad_hoc_listening",
    "read_earned_mentions", "read_public_reviews", "read_owned_posts",
    "read_owned_comments",
}


def validate_research_intake(payload: Any) -> dict[str, Any]:
    intake = require_object(payload, _INTAKE_KEYS, "$")
    if intake["schema_version"] != INTAKE_SCHEMA_VERSION:
        raise ContractError(f"$.schema_version must equal {INTAKE_SCHEMA_VERSION}")
    require_identifier(intake["research_id"], "$.research_id")
    require_timestamp(intake["created_at"], "$.created_at")
    route = require_enum(intake["workflow_route"], _ROUTES, "$.workflow_route")
    target = require_object(intake["target_audience"], _TARGET_KEYS, "$.target_audience")
    for key in _TARGET_KEYS - {"exclusions"}:
        require_string(target[key], f"$.target_audience.{key}")
    require_string_array(target["exclusions"], "$.target_audience.exclusions")
    require_enum(intake["audience_type"], _AUDIENCE_TYPES, "$.audience_type")
    require_enum(intake["research_depth"], _DEPTHS, "$.research_depth")
    require_string(intake["decision_to_support"], "$.decision_to_support")
    inputs = require_string_array(intake["available_inputs"], "$.available_inputs")
    unknown_inputs = sorted(set(inputs) - _INPUTS)
    if unknown_inputs:
        raise ContractError(
            f"$.available_inputs has unsupported values: {', '.join(unknown_inputs)}"
        )
    hints = require_string_array(
        intake["requested_or_supplied_connectors"],
        "$.requested_or_supplied_connectors",
        nonempty=True,
    )
    unknown_hints = sorted(set(hints) - _CONNECTOR_HINTS)
    if unknown_hints:
        raise ContractError(
            "$.requested_or_supplied_connectors has unsupported values: "
            + ", ".join(unknown_hints)
        )
    if "none" in hints and len(hints) != 1:
        raise ContractError(
            "$.requested_or_supplied_connectors may use none only by itself"
        )
    require_enum(
        intake["current_language_evidence"],
        _CURRENT_LANGUAGE,
        "$.current_language_evidence",
    )
    require_string_array(intake["languages"], "$.languages", nonempty=True)
    require_string(intake["as_of"], "$.as_of")
    existing = intake["existing_panel"]
    if route in {
        "refresh_existing_panel", "augment_existing_panel", "audit_existing_panel",
    }:
        if existing is None:
            raise ContractError(f"$.existing_panel is required for route {route}")
        existing = require_object(existing, _EXISTING_KEYS, "$.existing_panel")
        require_identifier(existing["panel_id"], "$.existing_panel.panel_id")
        require_string(existing["version"], "$.existing_panel.version")
        package_hash = require_string(
            existing["package_sha256"], "$.existing_panel.package_sha256"
        )
        if not package_hash.startswith("sha256:") or len(package_hash) != 71:
            raise ContractError("$.existing_panel.package_sha256 must be a SHA-256 fingerprint")
    elif existing is not None:
        raise ContractError(f"$.existing_panel must be null for route {route}")
    if route == "provisional_immediate_panel" and inputs:
        raise ContractError(
            "provisional_immediate_panel cannot claim supplied research inputs"
        )
    return dict(intake)


def validate_source_registry(payload: Any) -> dict[str, Any]:
    registry = require_object(payload, _REGISTRY_KEYS, "$")
    if registry["schema_version"] != REGISTRY_SCHEMA_VERSION:
        raise ContractError(f"$.schema_version must equal {REGISTRY_SCHEMA_VERSION}")
    require_timestamp(registry["updated_at"], "$.updated_at")
    seen: set[str] = set()
    for index, raw in enumerate(
        require_array(registry["source_families"], "$.source_families", nonempty=True)
    ):
        path = f"$.source_families[{index}]"
        family = require_object(raw, _FAMILY_KEYS, path)
        family_id = require_identifier(
            family["source_family_id"], f"{path}.source_family_id"
        )
        if family_id in seen:
            raise ContractError(f"{path}.source_family_id is duplicated")
        seen.add(family_id)
        require_enum(family["lane"], _LANES, f"{path}.lane")
        for key in (
            "name", "publisher", "source_kind", "cadence", "access_mode",
            "selection_notes", "connector",
        ):
            require_string(family[key], f"{path}.{key}")
        audience_types = require_string_array(
            family["audience_types"], f"{path}.audience_types", nonempty=True
        )
        if not set(audience_types).issubset(_AUDIENCE_TYPES):
            raise ContractError(f"{path}.audience_types contains an unsupported value")
        require_string_array(
            family["geographies"], f"{path}.geographies", nonempty=True
        )
        require_url(family["landing_url"], f"{path}.landing_url")
        require_url(family["methodology_url"], f"{path}.methodology_url")
        require_string_array(family["usable_for"], f"{path}.usable_for", nonempty=True)
        require_string_array(
            family["discovery_queries"], f"{path}.discovery_queries", nonempty=True
        )
    return dict(registry)


def _geography_class(geography: str) -> str:
    normalized = "".join(char for char in geography.casefold() if char.isalnum())
    if normalized in {"us", "usa", "unitedstates", "unitedstatesofamerica"}:
        return "us"
    return "global"


def _connector_selected(
    family: Mapping[str, Any],
    hints: set[str],
    inputs: set[str],
    capabilities: set[str],
) -> bool:
    connector = family["connector"]
    if connector == "social_listening_mcp":
        return bool(capabilities & _LISTENING_CAPABILITIES)
    if connector == "last30days_import":
        return (
            "last30days_json" in inputs
            and "last30days_json_import" in hints
        )
    if connector == "direct_web":
        return (
            "native_web_research" in hints
            and bool(capabilities & {"search_public_web", "read_public_web"})
        )
    if connector == "mapped_export":
        return bool(
            inputs & {"mapped_social_export", "social_listening_export"}
        )
    return False


def _first_party_selected(inputs: set[str]) -> bool:
    return bool(
        inputs
        & {
            "user_research", "interviews_aggregate", "sales_themes",
            "support_themes", "owned_social_aggregate",
            "first_party_evidence_package",
        }
    )


def _render_queries(
    family: Mapping[str, Any],
    target: Mapping[str, Any],
) -> list[str]:
    values = {
        "audience": target["audience"],
        "category": target["category"],
        "market": target["market"],
        "geography": target["geography"],
        "buying_context": target["buying_context"],
        "role": target["audience"],
        "industry": target["market"],
        "topic": target["category"],
        "technology": target["category"],
    }
    return [query.format_map(values) for query in family["discovery_queries"]]


def _research_questions(
    target: Mapping[str, Any],
    decision: str,
) -> list[str]:
    audience = target["audience"]
    context = target["buying_context"]
    category = target["category"]
    return [
        f"Which roles, industries, organization contexts, and buying roles materially define {audience}?",
        f"What responsibilities, workflows, KPIs, constraints, and pressures shape this audience now?",
        f"What events trigger {context} for {category}?",
        "What decision criteria, stakeholders, risks, and objections shape the choice?",
        "What proof, mechanisms, messengers, and evidence reduce perceived risk?",
        "Which channels, communities, creators, and content formats influence discovery or evaluation?",
        "What language, questions, workarounds, objections, and proof demands appear in current discussion?",
        "Which proposed traits and combinations are directly observed, estimated, or intentionally experimental?",
        f"What evidence is decision-relevant to {decision}, and what remains unsupported?",
    ]


def _evidence_basis(inputs: set[str], route: str) -> tuple[str, str]:
    if route in {"import_authorized_audience", "provisional_immediate_panel"}:
        return "none", "absent"
    first_party = _first_party_selected(inputs)
    public = True
    evidence_basis = "hybrid" if first_party and public else "first_party" if first_party else "public"
    performance = (
        "supplied" if "performance_evidence_package" in inputs else "absent"
    )
    return evidence_basis, performance


def _registry_freshness(
    registry: Mapping[str, Any],
    plan_created_at: str,
) -> dict[str, Any]:
    updated = require_timestamp(registry["updated_at"], "$.updated_at")
    planned = require_timestamp(plan_created_at, "$.created_at")
    age_days = max(0, (planned - updated).days)
    status = "review_due" if age_days > REGISTRY_REVIEW_AGE_DAYS else "current"
    warning = (
        "Source-family registry review is due. Verify every selected landing page, methodology page, edition, and connector assumption before retrieval."
        if status == "review_due"
        else ""
    )
    return {
        "updated_at": registry["updated_at"],
        "age_days": age_days,
        "review_after_days": REGISTRY_REVIEW_AGE_DAYS,
        "status": status,
        "warning": warning,
    }


def build_source_plan(
    intake_payload: Any,
    registry_payload: Any,
    capability_payload: Any,
) -> dict[str, Any]:
    intake = validate_research_intake(intake_payload)
    registry = validate_source_registry(registry_payload)
    capability_inventory = validate_capability_inventory(capability_payload)
    capabilities = verified_capabilities(capability_inventory)
    target = intake["target_audience"]
    audience_type = intake["audience_type"]
    geography_class = _geography_class(target["geography"])
    hints = set(intake["requested_or_supplied_connectors"])
    inputs = set(intake["available_inputs"])
    route = intake["workflow_route"]

    selected: list[dict[str, Any]] = []
    if route not in {
        "import_authorized_audience",
        "provisional_immediate_panel",
    }:
        for family in registry["source_families"]:
            lane = family["lane"]
            include = False
            if lane in {"structural", "survey"}:
                audience_match = (
                    audience_type == "mixed" or audience_type in family["audience_types"]
                )
                geography_match = (
                    "global" in family["geographies"]
                    or geography_class in family["geographies"]
                )
                include = audience_match and geography_match
            elif lane == "social_community":
                include = _connector_selected(family, hints, inputs, capabilities)
            elif lane == "first_party":
                include = _first_party_selected(inputs)
            elif lane == "performance":
                include = "performance_evidence_package" in inputs
            if not include:
                continue
            selected.append(
                {
                    "source_family_id": family["source_family_id"],
                    "lane": lane,
                    "name": family["name"],
                    "publisher": family["publisher"],
                    "source_kind": family["source_kind"],
                    "landing_url": family["landing_url"],
                    "methodology_url": family["methodology_url"],
                    "usable_for": list(family["usable_for"]),
                    "discovery_queries": _render_queries(family, target),
                    "selection_notes": family["selection_notes"],
                    "connector": family["connector"],
                }
            )
    selected.sort(key=lambda item: (item["lane"], item["source_family_id"]))

    if route == "provisional_immediate_panel":
        minimums: dict[str, int] = {}
    elif route == "import_authorized_audience":
        minimums = {"first_party": 1}
    else:
        minimums = dict(_DEPTH_MINIMUMS[intake["research_depth"]])
    current_language = intake["current_language_evidence"]
    if (
        current_language != "not_applicable"
        and route not in {
            "import_authorized_audience",
            "provisional_immediate_panel",
        }
    ):
        minimums["social_community"] = 1
    if route != "import_authorized_audience":
        if _first_party_selected(inputs):
            minimums["first_party"] = 1
        if "performance_evidence_package" in inputs:
            minimums["performance"] = 1
    lane_reasons = {
        "structural": "Ground roles, industries, organization context, geography, and defensible margins.",
        "survey": "Ground needs, attitudes, pressures, decisions, objections, proof needs, and media behavior.",
        "social_community": "Capture current language, questions, emerging objections, peer signals, and platform norms when relevant.",
        "first_party": (
            "Require Audience Data Lab privacy profiling, exact mapping approval, "
            "deterministic transformation, and a validated aggregate "
            "authorized-audience-handoff-v1."
            if route == "import_authorized_audience"
            else "Ground client-specific context from an approved aggregate evidence package."
        ),
        "performance": "Add approved historical outcome context without exposing private rows or current candidate performance.",
    }
    lane_requirements = [
        {
            "lane": lane,
            "minimum_sources": minimum,
            "required": (
                lane in {"structural", "survey"}
                or lane in {"first_party", "performance"}
                or (
                    lane == "social_community"
                    and current_language == "required"
                )
            ),
            "reason": lane_reasons[lane],
        }
        for lane, minimum in minimums.items()
    ]
    counts = {
        lane: sum(1 for family in selected if family["lane"] == lane)
        for lane in minimums
    }
    unresolved = [
        {
            "lane": lane,
            "required": next(
                item["required"]
                for item in lane_requirements
                if item["lane"] == lane
            ),
            "minimum_sources": minimum,
            "selected_source_families": counts[lane],
            "resolution": (
                "Send arbitrary authorized files to Audience Data Lab. Panel Builder "
                "may continue only from its validated aggregate "
                "authorized-audience-handoff-v1 after privacy profiling, mapping "
                "approval, and deterministic transformation."
                if route == "import_authorized_audience" and lane == "first_party"
                else "Verify a read capability, supply a permitted export, use native public research, or record a coverage gap."
                if lane == "social_community"
                else "Find directly relevant evidence or record an explicit gap."
            ),
        }
        for lane, minimum in minimums.items()
        if counts[lane] < minimum
    ]
    evidence_basis, performance_context = _evidence_basis(inputs, route)
    return {
        "schema_version": PLAN_SCHEMA_VERSION,
        "plan_id": f"{intake['research_id']}-source-plan",
        "created_at": intake["created_at"],
        "intake_sha256": sha256_json(intake),
        "capability_inventory_sha256": capability_inventory_sha256(
            capability_inventory
        ),
        "registry_freshness": _registry_freshness(
            registry,
            intake["created_at"],
        ),
        "workflow_route": route,
        "research_depth": intake["research_depth"],
        "evidence_basis": evidence_basis,
        "performance_context": performance_context,
        "target_audience": dict(target),
        "decision_to_support": intake["decision_to_support"],
        "research_questions": _research_questions(
            target, intake["decision_to_support"]
        ),
        "lane_requirements": lane_requirements,
        "selected_source_families": selected,
        "social_collection": {
            "requested_or_supplied_connectors": list(
                intake["requested_or_supplied_connectors"]
            ),
            "verified_read_capabilities": sorted(capabilities),
            "required_metadata": [
                "provider and collector", "query and filters",
                "window and timezone", "sort, limits, and pagination",
                "returned count and completeness",
                "deduplication and bot/spam controls",
                "access and permitted use",
            ],
            "prevalence_weight": 0,
        },
        "evidence_acceptance": [
            *(
                [
                    "This plan does not inspect, transform, or execute against supplied files.",
                    "Audience Data Lab owns arbitrary-file privacy profiling, mapping approval, and deterministic transformation.",
                    "Panel Builder accepts only a validated aggregate authorized-audience-handoff-v1.",
                ]
                if route == "import_authorized_audience"
                else []
            ),
            "Registry entries are planning templates, not evidence.",
            "Verify the exact edition, field dates, population, method, limitations, and permitted use.",
            "Trace each finding to exact accepted evidence items.",
            "Treat dependent reports of one upstream study as one evidence family.",
            "Use social evidence for qualitative context and emerging hypotheses, never audience prevalence or weights.",
            "Label every constructed attribute or combination observed, estimated, or experimental.",
        ],
        "unresolved_requirements": unresolved,
    }
