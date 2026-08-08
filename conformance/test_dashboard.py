from __future__ import annotations

from copy import deepcopy
import base64
import csv
from datetime import datetime, timedelta, timezone
import hashlib
import io
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SKILL_ROOT = ROOT / "skills" / "audience-ad-testing-lab"
sys.path.insert(0, str(SKILL_ROOT / "scripts"))

from audience_lab.dashboard import (  # noqa: E402
    DashboardInputError,
    V3_ALLOCATION_SCRIPT,
    V3_ALLOCATION_SECTION_HTML,
    _csv_safe,
    _is_deterministic_conformance_fixture,
    render_dashboard,
)
from audience_lab.audience_library import (  # noqa: E402
    audience_package_binding,
    materialize_provisional_audience,
    resolve_audience_panel,
)
from audience_lab.audience_package import build_audience_package  # noqa: E402
from audience_lab.dispatch import enrich_assignment_jobs  # noqa: E402
from conformance import test_v3_dispatch_compatibility as dispatch_harness  # noqa: E402
from conformance import test_v3_profile_rosters as roster_harness  # noqa: E402


FIXTURE = ROOT / "conformance" / "fixtures" / "dashboard-study"
COPY_ONLY_FIXTURE = FIXTURE / "copy-only"
PAYLOAD_RE = re.compile(
    r'<script type="application/json" id="audience-lab-data">(.*?)</script>',
    re.S,
)
LEGACY_DASHBOARD_SHA256 = {
    "v1_partial": "dd002ea57df4f54b5de38edeeab6179b91299ecb7996a43d4efd318c45d687c2",
    "v1_complete": "6ba660269aa6ca09015f2f42a0ccf1cdc45a3cb5b166c835123d3525a88332aa",
    "v2_complete": "ecd5aa53899c7fbacf90eeb99b72a72c32ac65c7d72d1da0835418c688f6b4b0",
}


def render_fixture_dashboard(
    run_dir: Path = FIXTURE, include_saliency: bool = False
) -> str:
    with tempfile.TemporaryDirectory() as temp_dir:
        output = Path(temp_dir) / "dashboard.html"
        render_dashboard(
            run_dir=run_dir,
            template_path=SKILL_ROOT / "assets" / "dashboard-template.html",
            output_path=output,
            include_saliency=include_saliency,
        )
        return output.read_text(encoding="utf-8")


def payload_from(html: str) -> dict:
    match = PAYLOAD_RE.search(html)
    if not match:
        raise AssertionError("dashboard JSON payload not found")
    return json.loads(match.group(1))


def copy_fixture(destination: Path, source: Path = FIXTURE) -> Path:
    run_dir = destination / "dashboard-study"
    shutil.copytree(source, run_dir)
    return run_dir


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def render_to(run_dir: Path, output: Path) -> None:
    from conformance.test_tier4_held_out_evaluation import (
        _AUTHORITY_REGISTRIES,
    )
    render_dashboard(
        run_dir,
        SKILL_ROOT / "assets" / "dashboard-template.html",
        output,
        authority_registry=_AUTHORITY_REGISTRIES,
    )


def validator_result(output: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SKILL_ROOT / "scripts" / "validate-dashboard.py"), str(output)],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )


def rewrite_dashboard_payload(output: Path, mutate) -> None:
    html = output.read_text(encoding="utf-8")
    payload = payload_from(html)
    mutate(payload)
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    encoded = encoded.replace("&", r"\u0026").replace("<", r"\u003c").replace(">", r"\u003e")
    output.write_text(PAYLOAD_RE.sub(lambda _: f'<script type="application/json" id="audience-lab-data">{encoded}</script>', html), encoding="utf-8")


def replace_json_export(payload: dict, filename: str, mutate) -> None:
    export = next(item for item in payload["exports"] if item["filename"] == filename)
    prefix, encoded = export["data_url"].split(",", 1)
    source = json.loads(base64.b64decode(encoded).decode("utf-8"))
    mutate(source)
    export["data_url"] = prefix + "," + base64.b64encode(
        (json.dumps(source, indent=2) + "\n").encode("utf-8")
    ).decode("ascii")


def replace_export_bytes(payload: dict, filename: str, mutate) -> None:
    export = next(item for item in payload["exports"] if item["filename"] == filename)
    prefix, encoded = export["data_url"].split(",", 1)
    export["data_url"] = prefix + "," + base64.b64encode(
        mutate(base64.b64decode(encoded))
    ).decode("ascii")


def _decode_test_data_url(value: str) -> tuple[bytes, str]:
    metadata, encoded = value.split(",", 1)
    return base64.b64decode(encoded), metadata[5:].split(";", 1)[0]


def attach_research_backed_audience(run_dir: Path) -> None:
    fixtures = ROOT / "conformance" / "fixtures" / "audience-research"
    brief = read_json(fixtures / "approved-brief.json")
    panel = read_json(fixtures / "approved-panel.json")
    package = build_audience_package(brief, panel, run_dir.parent / "audience-package")
    scope = {
        key: deepcopy(panel["audience_scope"][key])
        for key in ("audience", "market", "geography", "category", "buying_context", "exclusions")
    }
    resolution = resolve_audience_panel(
        {"source": "file", "package_path": str(package.package_zip_path)},
        scope,
        run_dir=run_dir,
    )
    manifest_path = run_dir / "study-manifest.json"
    manifest = read_json(manifest_path)
    manifest["audience_lock"] = resolution["audience_lock"]
    manifest["audience_package"] = audience_package_binding(run_dir, resolution)
    write_json(manifest_path, manifest)
    response_path = run_dir / "panelist-responses.jsonl"
    responses = [json.loads(line) for line in response_path.read_text().splitlines() if line]
    for response in responses:
        response["segment_id"] = "operations-leaders"
        response["persona_archetype_id"] = "evidence-led-operator"
    response_path.write_text(
        "".join(json.dumps(item, separators=(",", ":")) + "\n" for item in responses),
        encoding="utf-8",
    )
    feedback_path = run_dir / "feedback-synthesis.json"
    feedback = read_json(feedback_path)
    for theme in feedback["themes"]:
        theme["segment_id"] = "operations-leaders"
    write_json(feedback_path, feedback)


def attach_provisional_audience(run_dir: Path) -> None:
    now = datetime.now(timezone.utc).replace(microsecond=0)
    resolution = materialize_provisional_audience(
        {
            "scope": {
                "audience": "Marketing leaders evaluating campaign tools",
                "market": "B2B software",
                "geography": "United States",
                "category": "Campaign software",
                "buying_context": "Early evaluation",
                "exclusions": [],
            },
            "user_defined_segments": [
                {
                    "segment_id": "marketing-leaders",
                    "name": "Marketing leaders",
                    "description": "User-defined planning segment for this run.",
                }
            ],
            "accepted_by": "study-owner",
            "accepted_at": (now - timedelta(days=1)).isoformat().replace("+00:00", "Z"),
            "expires_at": (now + timedelta(days=20)).isoformat().replace("+00:00", "Z"),
        },
        run_dir=run_dir,
        now=now,
    )
    manifest_path = run_dir / "study-manifest.json"
    manifest = read_json(manifest_path)
    manifest["audience_lock"] = resolution["audience_lock"]
    manifest["audience_package"] = audience_package_binding(run_dir, resolution)
    write_json(manifest_path, manifest)
    response_path = run_dir / "panelist-responses.jsonl"
    responses = [json.loads(line) for line in response_path.read_text().splitlines() if line]
    for response in responses:
        response["segment_id"] = "marketing-leaders"
        response["persona_archetype_id"] = "marketing-leaders-provisional-mindset"
    response_path.write_text(
        "".join(json.dumps(item, separators=(",", ":")) + "\n" for item in responses),
        encoding="utf-8",
    )
    feedback_path = run_dir / "feedback-synthesis.json"
    feedback = read_json(feedback_path)
    for theme in feedback["themes"]:
        theme["segment_id"] = "marketing-leaders"
    write_json(feedback_path, feedback)


def _canonical_job_bytes(payload: dict) -> bytes:
    return (json.dumps(payload, indent=2, sort_keys=True) + "\n").encode("utf-8")


def _job_binding(path: str, payload: dict) -> dict:
    raw = _canonical_job_bytes(payload)
    return {
        "status": "dispatched",
        "path": path,
        "content_hash": "sha256:" + hashlib.sha256(raw).hexdigest(),
        "record_count": len(payload["synthetic_replicate_jobs"]),
    }


def _rewrite_fixture_study_id(run_dir: Path, study_id: str) -> None:
    for filename in (
        "creative-roster.json",
        "screening-model-results.json",
        "boundary-results.json",
        "finalist-results.json",
        "feedback-synthesis.json",
        "saliency-index.json",
    ):
        path = run_dir / filename
        if not path.is_file():
            continue
        payload = read_json(path)
        payload["study_id"] = study_id
        write_json(path, payload)
    response_path = run_dir / "panelist-responses.jsonl"
    responses = [
        json.loads(line)
        for line in response_path.read_text(encoding="utf-8").splitlines()
        if line
    ]
    for response in responses:
        response["study_id"] = study_id
    response_path.write_text(
        "".join(
            json.dumps(response, separators=(",", ":")) + "\n"
            for response in responses
        ),
        encoding="utf-8",
    )


def _rewrite_fixture_creative_ids(run_dir: Path) -> None:
    replacements = {
        "creative-a": "creative-1",
        "creative-b": "creative-2",
        "creative-c": "creative-3",
        "creative-d": "creative-4",
        "copy-a": "creative-1",
        "copy-b": "creative-2",
    }
    media_dir = run_dir / "media"
    if media_dir.is_dir():
        for old, new in replacements.items():
            for suffix in (".svg", "-overlay.svg"):
                source = media_dir / f"{old}{suffix}"
                if source.is_file():
                    shutil.copyfile(source, media_dir / f"{new}{suffix}")

    def rewrite(value):
        if isinstance(value, str):
            for old, new in replacements.items():
                value = value.replace(old, new)
            return value
        if isinstance(value, list):
            return [rewrite(item) for item in value]
        if isinstance(value, dict):
            return {rewrite(key): rewrite(item) for key, item in value.items()}
        return value

    for path in run_dir.glob("*.json"):
        write_json(path, rewrite(read_json(path)))
    for path in run_dir.glob("*.jsonl"):
        records = [
            rewrite(json.loads(line))
            for line in path.read_text(encoding="utf-8").splitlines()
            if line
        ]
        path.write_text(
            "".join(
                json.dumps(record, separators=(",", ":")) + "\n"
                for record in records
            ),
            encoding="utf-8",
        )


def _expand_fixture_to_seven_creatives(run_dir: Path) -> None:
    roster_path = run_dir / "creative-roster.json"
    roster = read_json(roster_path)
    source = next(
        creative
        for creative in roster["creatives"]
        if creative["variation_id"] == "creative-4"
    )
    for number in range(5, 8):
        creative = deepcopy(source)
        creative["variation_id"] = f"creative-{number}"
        creative["display_name"] = f"Control concept {number}"
        creative["headline"] = f"Control message {number}"
        roster["creatives"].append(creative)
    write_json(roster_path, roster)

    screening_path = run_dir / "screening-model-results.json"
    screening = read_json(screening_path)
    for number in range(5, 8):
        creative_id = f"creative-{number}"
        screening["utilities"][creative_id] = -1.37 - number / 100
        screening["ranked_ids"].append(creative_id)
        screening["top_k_inclusion_frequencies"][creative_id] = 0.0
        screening["classifications"][creative_id] = "unresolved"
        screening["model_diagnostics"]["usable_participations_per_creative"][
            creative_id
        ] = 1
    write_json(screening_path, screening)

    response_path = run_dir / "panelist-responses.jsonl"
    responses = [
        json.loads(line)
        for line in response_path.read_text(encoding="utf-8").splitlines()
        if line
    ]
    for response in responses:
        if response["record_type"] != "screening_response":
            continue
        for number in range(5, 8):
            creative_id = f"creative-{number}"
            response["assigned_variation_ids"].append(creative_id)
            response["shown_order"].append(creative_id)
            response["per_creative_reactions"].append(
                {
                    "variation_id": creative_id,
                    "immediate_reaction": "",
                    "judgment_status": "unable_to_judge",
                }
            )
    response_path.write_text(
        "".join(
            json.dumps(response, separators=(",", ":")) + "\n"
            for response in responses
        ),
        encoding="utf-8",
    )


def _canonical_response_for_v3_job(job: dict, *, usable: bool = True) -> dict:
    response = {
        key: deepcopy(job[key])
        for key in (
            "study_id",
            "response_id",
            "record_type",
            "method",
            "synthetic_replicate_id",
            "persona_archetype_id",
            "segment_id",
            "profile_snapshot",
            "context_attribute_provenance",
            "worker_context_isolation",
            "human_sample_independence",
            "audience_slot_id",
            "grounded_profile_id",
            "profile_snapshot_sha256",
        )
    }
    response.update(
        {
            "reviewer_dispatch_id": job["dispatch_id"],
            "assigned_variation_ids": list(job["variation_ids"]),
            "blind_labels": deepcopy(job["blind_labels"]),
            "shown_order": list(job["shown_order"]),
            "reaction_protocol": job["reaction_protocol"],
            "validation": {
                "schema_valid": True,
                "assignment_valid": True,
                "reaction_order_valid": True,
            },
        }
    )
    if "context_stratum_id" in job:
        response["context_stratum_id"] = job["context_stratum_id"]

    attempts: list[dict] = []
    reactions: list[dict] = []
    for position, creative_id in enumerate(response["shown_order"], 1):
        provider_return_id = (
            f"provider-{response['synthetic_replicate_id']}-reaction-{position}"
        )
        attempts.append(
            {
                "attempt_id": provider_return_id,
                "stage": "reaction",
                "position_seen": position,
                "attempt_number": 1,
                "provider_return_id": provider_return_id,
                "outcome": "accepted",
                "validation_errors": [],
            }
        )
        reaction = {
            "reaction_id": (
                f"reaction-{response['synthetic_replicate_id']}-{position}"
            ),
            "variation_id": creative_id,
            "display_label_seen": response["blind_labels"][creative_id],
            "position_seen": position,
            "reaction_label": "immediate",
            "immediate_reaction": "A concrete, evidence-bound synthetic reaction.",
            "judgment_status": "judged",
            "source_provenance": {
                "provider_return_id": provider_return_id,
                "capture": "verbatim_provider_return",
            },
        }
        if response["record_type"] != "finalist_response":
            reaction.update(
                {
                    "noticed_or_understood_first": "The central claim.",
                    "strongest_positive_signal": "The concrete framing.",
                    "strongest_negative_signal": "The proof remains to be tested.",
                }
            )
        reactions.append(reaction)
    if not usable and reactions:
        reactions[-1]["judgment_status"] = "unable_to_judge"

    comparison_return_id = (
        f"provider-{response['synthetic_replicate_id']}-comparison"
    )
    attempts.append(
        {
            "attempt_id": comparison_return_id,
            "stage": "comparison",
            "attempt_number": 1,
            "provider_return_id": comparison_return_id,
            "outcome": "accepted",
            "validation_errors": [],
        }
    )
    response["runtime_attempts"] = attempts
    comparison_source = {
        "provider_return_id": comparison_return_id,
        "capture": "verbatim_provider_return",
    }
    frozen_ids = [reaction["reaction_id"] for reaction in reactions]

    if response["record_type"] == "screening_response":
        response["per_creative_reactions"] = reactions
        if response["method"] == "complete_exposure":
            response["complete_set_evaluation"] = {
                "status": "ranked",
                "preference_ranking": list(response["assigned_variation_ids"]),
                "frozen_reaction_ids": frozen_ids,
                "source_provenance": comparison_source,
            }
            response["usable_complete_exposure_observation"] = True
        else:
            response["comparative_choice"] = (
                {
                    "status": "best_worst",
                    "best_variation_id": response["assigned_variation_ids"][0],
                    "weakest_variation_id": response["assigned_variation_ids"][-1],
                    "best_reason": "It gives the clearest concrete frame.",
                    "weakest_reason": "It needs the most proof in a live test.",
                    "frozen_reaction_ids": frozen_ids,
                    "source_provenance": comparison_source,
                }
                if usable
                else {
                    "status": "unable_to_judge",
                    "best_variation_id": "",
                    "weakest_variation_id": "",
                    "frozen_reaction_ids": frozen_ids,
                    "source_provenance": comparison_source,
                }
            )
            response["usable_maxdiff_block"] = usable
    elif response["record_type"] == "boundary_response":
        response["per_creative_reactions"] = reactions
        response["pairwise_choice"] = {
            "status": "tie",
            "preferred_variation_id": "",
            "reason": "The evidence supports no meaningful directional separation.",
            "frozen_reaction_ids": frozen_ids,
            "source_provenance": comparison_source,
        }
        response["usable_pairwise_observation"] = True
    else:
        for reaction in reactions:
            reaction["rubric_scores"] = {
                key: 4
                for key in (
                    "comprehension",
                    "relevance",
                    "credibility",
                    "offer_appeal",
                    "motivation",
                    "friction",
                    "attention_potential",
                    "overall",
                )
            }
            reaction["feedback"] = ["Preserve the concrete proof."]
            reaction["rubric_source_provenance"] = comparison_source
        response["finalist_reviews"] = reactions
        response["final_preference_ranking"] = list(
            response["assigned_variation_ids"]
        )
    return response


def _bind_v3_fixture_responses_to_jobs(run_dir: Path, jobs: dict[str, dict]) -> None:
    response_path = run_dir / "panelist-responses.jsonl"
    original_responses = [
        json.loads(line)
        for line in response_path.read_text(encoding="utf-8").splitlines()
        if line
    ]
    feedback_path = run_dir / "feedback-synthesis.json"
    feedback = read_json(feedback_path)
    jobs_by_type: dict[str, list[dict]] = {}
    for envelope in jobs.values():
        for job in envelope["synthetic_replicate_jobs"]:
            jobs_by_type.setdefault(job["record_type"], []).append(job)

    old_to_new: dict[str, str] = {}
    used_replicates: set[str] = set()
    responses: list[dict] = []
    for original in original_responses:
        required_creatives = {
            theme["creative_id"]
            for theme in feedback["themes"]
            if original["response_id"] in theme["response_ids"]
        }
        available = [
            job
            for job in jobs_by_type[original["record_type"]]
            if job["synthetic_replicate_id"] not in used_replicates
        ]
        job = next(
            (
                candidate
                for candidate in available
                if required_creatives.issubset(candidate["variation_ids"])
            ),
            available[0],
        )
        used_replicates.add(job["synthetic_replicate_id"])
        usable = original.get(
            "usable_complete_exposure_observation",
            original.get(
                "usable_maxdiff_block",
                original.get("usable_pairwise_observation", True),
            ),
        )
        response = _canonical_response_for_v3_job(job, usable=usable is True)
        old_to_new[original["response_id"]] = response["response_id"]
        responses.append(response)

    response_path.write_text(
        "".join(
            json.dumps(response, separators=(",", ":")) + "\n"
            for response in responses
        ),
        encoding="utf-8",
    )
    response_by_id = {response["response_id"]: response for response in responses}
    response_type_by_stage = {
        "screening": "screening_response",
        "boundary": "boundary_response",
        "finalist": "finalist_response",
    }
    for theme in feedback["themes"]:
        theme["segment_id"] = "operations-leaders"
        theme["response_ids"] = [
            old_to_new[response_id] for response_id in theme["response_ids"]
        ]
    covered_creatives = {
        theme["creative_id"]
        for theme in feedback["themes"]
        if theme["feedback_type"] in {"strength", "friction"}
    }
    strength_template = next(
        theme
        for theme in feedback["themes"]
        if theme["stage"] == "screening"
        and theme["feedback_type"] == "strength"
    )
    for response in responses:
        if response["record_type"] != "screening_response":
            continue
        for reaction in response["per_creative_reactions"]:
            creative_id = reaction["variation_id"]
            if (
                reaction["judgment_status"] != "judged"
                or creative_id in covered_creatives
            ):
                continue
            extra_theme = deepcopy(strength_template)
            extra_theme.update(
                {
                    "creative_id": creative_id,
                    "segment_id": response["segment_id"],
                    "theme": (
                        f"{creative_id} produced a usable written synthetic reaction."
                    ),
                    "response_ids": [response["response_id"]],
                }
            )
            feedback["themes"].append(extra_theme)
            covered_creatives.add(creative_id)
    for theme in feedback["themes"]:
        theme["exposed_base"]["count"] = sum(
            response["record_type"] == response_type_by_stage[theme["stage"]]
            and response["segment_id"] == theme["segment_id"]
            and theme["creative_id"] in response["assigned_variation_ids"]
            for response in response_by_id.values()
        )
    write_json(feedback_path, feedback)
    finalist_result_path = run_dir / "finalist-results.json"
    finalist_result = read_json(finalist_result_path)
    finalist_result["total_model_calls"] = sum(
        len(response["runtime_attempts"])
        for response in responses
        if response["record_type"] == "finalist_response"
    )
    write_json(finalist_result_path, finalist_result)


def attach_v3_allocation_run(
    run_dir: Path,
    build_root: Path,
    *,
    bundle: str = "tier_2",
    documents: dict | None = None,
    allow_directional: bool = True,
    boundary_waves: int = 1,
) -> tuple[dict, dict[str, dict], Path]:
    _rewrite_fixture_creative_ids(run_dir)
    harness = roster_harness.V3ProfileRosterTests()
    harness.setUp()
    complete_exposure = not (run_dir / "boundary-results.json").is_file()
    if not complete_exposure:
        _expand_fixture_to_seven_creatives(run_dir)
    plan, source_resolution = harness._valid_plan(
        build_root,
        bundle=bundle,
        documents=documents,
        allow_directional=allow_directional,
        creative_count=2 if complete_exposure else 7,
        maximum_synthetic_panelists=20 if complete_exposure else 40,
        requested_shortlist_size=2 if complete_exposure else 3,
    )
    source_audience = source_resolution.parent
    destination_audience = run_dir / "audience"
    shutil.copytree(source_audience, destination_audience)
    resolution_path = destination_audience / "resolution.json"

    screening = enrich_assignment_jobs(
        plan,
        dispatch_harness._dispatch_context(plan, "screening_response"),
        audience_resolution=resolution_path,
        allow_directional_allocation=allow_directional,
    )
    manifest = dispatch_harness._manifest_from_plan(plan)
    jobs: dict[str, dict] = {"screening-jobs.json": screening}
    boundary_bindings: list[dict] = []
    if not complete_exposure:
        boundary_authority = dispatch_harness._boundary_authority(plan)
        prior = None
        for wave in range(1, boundary_waves + 1):
            context = dispatch_harness._dispatch_context(
                plan, "boundary_response"
            )
            context["boundary_waves"] = [wave]
            prior_responses = None
            prior_boundary_result = None
            if prior is not None:
                (
                    prior_responses,
                    prior_boundary_result,
                ) = dispatch_harness._boundary_continuation_evidence(
                    plan,
                    prior,
                )
            envelope = enrich_assignment_jobs(
                boundary_authority,
                context,
                manifest=manifest,
                audience_resolution=resolution_path,
                allow_directional_allocation=allow_directional,
                prior_jobs_envelope=prior,
                prior_responses=prior_responses,
                prior_boundary_result=prior_boundary_result,
            )
            filename = f"boundary-wave-{wave:04d}-jobs.json"
            jobs[filename] = envelope
            boundary_bindings.append(
                {
                    "wave": wave,
                    **{
                        key: value
                        for key, value in _job_binding(filename, envelope).items()
                        if key != "status"
                    },
                }
            )
            prior = envelope

    creative_ids = sorted(manifest["outputs"]["creative_asset_hashes"])
    approved_count = 2 if complete_exposure else 3
    fixture_finalists = read_json(run_dir / "finalist-results.json")
    approval = {
        "study_id": plan["study_id"],
        "method": plan["method"],
        "approved_finalist_ids": creative_ids[:approved_count],
        "roster_decision": fixture_finalists["roster_decision"],
    }
    finalist_context = dispatch_harness._dispatch_context(
        plan, "finalist_response"
    )
    finalist_context["requested_job_slots"] = 4 if complete_exposure else 2
    finalist = enrich_assignment_jobs(
        approval,
        finalist_context,
        manifest=manifest,
        audience_resolution=resolution_path,
        allow_directional_allocation=allow_directional,
    )
    jobs["finalist-jobs.json"] = finalist
    for filename, payload in jobs.items():
        (run_dir / filename).write_bytes(_canonical_job_bytes(payload))

    original_manifest = read_json(run_dir / "study-manifest.json")
    manifest.update(
        {
            "creative_format": original_manifest["creative_format"],
            "study_objective": original_manifest.get("study_objective", ""),
            "runtime": original_manifest["runtime"],
            "external_validity": original_manifest["external_validity"],
            "validity_status": original_manifest["validity_status"],
            "validity_reasons": read_json(
                run_dir / "screening-model-results.json"
            ).get("validity_reasons", []),
        }
    )
    if "usage" in original_manifest:
        manifest["usage"] = original_manifest["usage"]
    manifest["outputs"]["audience_allocation_jobs"] = {
        "schema_version": "audience-allocation-jobs-index-v1",
        "screening": _job_binding("screening-jobs.json", screening),
        "boundary": (
            {
                "status": "not_applicable",
                "reason": "method_complete_exposure",
            }
            if complete_exposure
            else {"status": "dispatched", "waves": boundary_bindings}
        ),
        "finalist": _job_binding("finalist-jobs.json", finalist),
    }
    write_json(run_dir / "study-manifest.json", manifest)
    _rewrite_fixture_study_id(run_dir, plan["study_id"])
    if not complete_exposure:
        screening_path = run_dir / "screening-model-results.json"
        screening_result = read_json(screening_path)
        screening_result["boundary_plan"] = boundary_authority["boundary_plan"]
        write_json(screening_path, screening_result)
    if not complete_exposure:
        finalist_path = run_dir / "finalist-results.json"
        finalist_result = read_json(finalist_path)
        finalist_result["approved_finalist_ids"].append("creative-3")
        finalist_result["first_choice_counts"]["creative-3"] = 0
        finalist_result["conditional_first_choice_share"]["creative-3"] = 0.0
        finalist_result["rubric_summary"]["creative-3"] = deepcopy(
            finalist_result["rubric_summary"]["creative-2"]
        )
        finalist_result["testing_map"].append(
            {
                "creative_id": "creative-3",
                "role": "proof challenger",
                "next_test": "Test a more specific supporting proof point.",
            }
        )
        write_json(finalist_path, finalist_result)
        feedback_path = run_dir / "feedback-synthesis.json"
        feedback = read_json(feedback_path)
        for feedback_type in ("friction", "next_test"):
            extra_theme = deepcopy(
                next(
                    theme
                    for theme in feedback["themes"]
                    if theme["stage"] == "finalist"
                    and theme["creative_id"] == "creative-2"
                    and theme["feedback_type"] == feedback_type
                )
            )
            extra_theme["creative_id"] = "creative-3"
            extra_theme["theme"] = (
                "The proof challenger creates a distinct supporting-proof comparison."
            )
            feedback["themes"].append(extra_theme)
        write_json(feedback_path, feedback)
    _bind_v3_fixture_responses_to_jobs(run_dir, jobs)
    return plan, jobs, resolution_path


class DashboardV3AllocationTests(unittest.TestCase):
    maxDiff = None

    def test_reusable_tier_one_reports_directional_allocation_without_frame_fidelity(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            run_dir = copy_fixture(root, FIXTURE)
            attach_v3_allocation_run(
                run_dir,
                root / "v3",
                bundle="tier_1",
                allow_directional=True,
            )
            html = render_fixture_dashboard(run_dir)
            allocation = payload_from(html)["audience"]["run_allocation"]

        self.assertEqual("tier_1", allocation["package"]["tier"])
        self.assertEqual(
            {
                "structural": ["planning_allocation"],
                "overlay": ["planning_allocation"],
            },
            allocation["reusable_weight_semantics"],
        )
        screening = allocation["stages"][0]
        self.assertEqual("screening", screening["stage"])
        selected = next(
            scope
            for scope in screening["diagnostics"]
            if scope["diagnostic_scope"] == "selected_for_dispatch"
        )
        self.assertEqual(
            "directional_profile_allocation",
            selected["fidelity_status"],
        )
        self.assertEqual(
            "This reusable Tier 1 panel allocates synthetic panelists across "
            "approved profiles using directional planning allocations. It "
            "does not claim population composition.",
            selected["claim_language"],
        )
        self.assertNotIn("maximum_absolute_deviation", selected)
        self.assertNotIn(
            "product allocation threshold",
            json.dumps(allocation).lower(),
        )
        self.assertEqual(
            "This run remains a Tier 1 directional creative hypothesis stress "
            "test even though the saved panel retains its approved reusable tier.",
            selected["user_decision"],
        )
        self.assertEqual(
            "This is not a human sample or a customer survey.",
            allocation["disclaimer"],
        )

    def test_partial_reserve_displays_full_and_selected_scope_with_selected_authority(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            run_dir = copy_fixture(root, FIXTURE)
            plan, _jobs, _resolution = attach_v3_allocation_run(
                run_dir,
                root / "v3",
            )
            allocation = payload_from(
                render_fixture_dashboard(run_dir)
            )["audience"]["run_allocation"]

        boundary = next(
            stage
            for stage in allocation["stages"]
            if stage["stage"] == "boundary"
        )
        self.assertEqual("dispatched", boundary["dispatch_status"])
        self.assertEqual(
            ["full_reserve", "selected_for_dispatch"],
            [
                diagnostic["diagnostic_scope"]
                for diagnostic in boundary["diagnostics"]
            ],
        )
        full, selected = boundary["diagnostics"]
        self.assertEqual(
            len(
                plan["audience_profile_rosters"]["boundary_reserve"][
                    "assignments"
                ]
            ),
            full["requested_slot_count"],
        )
        self.assertEqual(4, selected["requested_slot_count"])
        self.assertFalse(full["run_claim_authority"])
        self.assertTrue(selected["run_claim_authority"])
        self.assertEqual(
            "Selected-for-dispatch diagnostics are the run-claim authority.",
            selected["authority_label"],
        )
        self.assertEqual(
            plan["audience_profile_rosters"]["boundary_reserve"][
                "structural_group_diagnostics"
            ],
            full["structural_groups"],
        )
        screening = allocation["stages"][0]["diagnostics"][-1]
        self.assertEqual(
            "The synthetic roster is aligned to the approved structural "
            "frame within the product allocation threshold.",
            screening["claim_language"],
        )
        self.assertEqual(
            "The requested synthetic capacity cannot preserve the approved "
            "structural composition within the product threshold.",
            selected["claim_language"],
        )
        self.assertEqual(
            "This run remains a Tier 1 directional creative hypothesis stress "
            "test even though the saved panel retains its approved reusable tier.",
            selected["user_decision"],
        )
        allocation_text = json.dumps(allocation).lower()
        self.assertNotIn("margin of error", allocation_text)
        self.assertNotIn("survey result", allocation_text)
        self.assertNotIn("measured performance", allocation_text)
        self.assertNotIn("confidence", allocation_text)

    def test_complete_exposure_boundary_is_exactly_not_applicable(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            run_dir = copy_fixture(root, COPY_ONLY_FIXTURE)
            attach_v3_allocation_run(run_dir, root / "v3")
            allocation = payload_from(
                render_fixture_dashboard(run_dir)
            )["audience"]["run_allocation"]

        boundary = next(
            stage
            for stage in allocation["stages"]
            if stage["stage"] == "boundary"
        )
        self.assertEqual("not_applicable", boundary["dispatch_status"])
        self.assertEqual(
            "Not applicable — complete exposure has no boundary stage",
            boundary["message"],
        )
        self.assertNotIn("diagnostics", boundary)
        self.assertNotIn("fidelity_status", boundary)
        self.assertNotIn("must_cover", boundary)
        self.assertNotIn("%", json.dumps(boundary))

    def test_v3_allocation_index_and_bound_job_envelopes_fail_closed(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            base = copy_fixture(root / "base", FIXTURE)
            _plan, jobs, _resolution = attach_v3_allocation_run(
                base,
                root / "v3",
                boundary_waves=2,
            )

            def run_case(label, mutate, pattern):
                case = root / label
                shutil.copytree(base, case)
                mutate(case)
                with self.assertRaisesRegex(DashboardInputError, pattern):
                    render_to(case, root / f"{label}.html")

            def mutate_manifest(case: Path, mutate) -> None:
                path = case / "study-manifest.json"
                manifest = read_json(path)
                mutate(manifest)
                write_json(path, manifest)

            cases = {
                "missing_index": (
                    lambda case: mutate_manifest(
                        case,
                        lambda manifest: manifest["outputs"].pop(
                            "audience_allocation_jobs"
                        ),
                    ),
                    "audience_allocation_jobs",
                ),
                "extra_index_key": (
                    lambda case: mutate_manifest(
                        case,
                        lambda manifest: manifest["outputs"][
                            "audience_allocation_jobs"
                        ].update(extra="forbidden"),
                    ),
                    "allowlist",
                ),
                "wrong_screening_path": (
                    lambda case: mutate_manifest(
                        case,
                        lambda manifest: manifest["outputs"][
                            "audience_allocation_jobs"
                        ]["screening"].update(path="jobs.json"),
                    ),
                    "screening-jobs.json",
                ),
                "wrong_screening_hash": (
                    lambda case: mutate_manifest(
                        case,
                        lambda manifest: manifest["outputs"][
                            "audience_allocation_jobs"
                        ]["screening"].update(
                            content_hash="sha256:" + "0" * 64
                        ),
                    ),
                    "content_hash",
                ),
                "wrong_screening_count": (
                    lambda case: mutate_manifest(
                        case,
                        lambda manifest: manifest["outputs"][
                            "audience_allocation_jobs"
                        ]["screening"].update(record_count=999),
                    ),
                    "record_count",
                ),
                "decision_bound_as_dispatched": (
                    lambda case: (
                        (case / "screening-jobs.json").write_bytes(
                            _canonical_job_bytes(
                                jobs["screening-jobs.json"][
                                    "audience_allocation_subset"
                                ]
                            )
                        ),
                        mutate_manifest(
                            case,
                            lambda manifest: manifest["outputs"][
                                "audience_allocation_jobs"
                            ]["screening"].update(
                                content_hash="sha256:"
                                + hashlib.sha256(
                                    (case / "screening-jobs.json").read_bytes()
                                ).hexdigest(),
                                record_count=0,
                            ),
                        ),
                    ),
                    "worker-ready|successful",
                ),
                "screening_not_dispatched_with_responses": (
                    lambda case: mutate_manifest(
                        case,
                        lambda manifest: manifest["outputs"][
                            "audience_allocation_jobs"
                        ].update(
                            screening={"status": "not_dispatched"}
                        ),
                    ),
                    "screening.*not_dispatched",
                ),
                "boundary_wave_missing": (
                    lambda case: (case / "boundary-wave-0001-jobs.json").unlink(),
                    "boundary-wave-0001-jobs.json",
                ),
                "boundary_wave_skipped": (
                    lambda case: mutate_manifest(
                        case,
                        lambda manifest: manifest["outputs"][
                            "audience_allocation_jobs"
                        ]["boundary"]["waves"].pop(0),
                    ),
                    "contiguous|wave 1",
                ),
                "boundary_wave_reordered": (
                    lambda case: mutate_manifest(
                        case,
                        lambda manifest: manifest["outputs"][
                            "audience_allocation_jobs"
                        ]["boundary"]["waves"].reverse(),
                    ),
                    "ordered|contiguous",
                ),
                "boundary_wave_tampered": (
                    lambda case: (
                        case / "boundary-wave-0001-jobs.json"
                    ).write_bytes(
                        (case / "boundary-wave-0001-jobs.json").read_bytes()
                        + b" "
                    ),
                    "content_hash",
                ),
                "boundary_later_wave_without_prior": (
                    lambda case: mutate_manifest(
                        case,
                        lambda manifest: manifest["outputs"][
                            "audience_allocation_jobs"
                        ]["boundary"].update(
                            waves=manifest["outputs"][
                                "audience_allocation_jobs"
                            ]["boundary"]["waves"][1:]
                        ),
                    ),
                    "wave 1|prior",
                ),
            }
            for label, (mutate, pattern) in cases.items():
                with self.subTest(label=label):
                    run_case(label, mutate, pattern)

    def test_not_dispatched_status_must_match_stage_results_and_responses(
        self,
    ) -> None:
        stage_cases = (
            ("screening", "screening"),
            ("boundary", "boundary"),
            ("finalist", "finalist"),
        )
        for label, stage in stage_cases:
            with self.subTest(stage=stage), tempfile.TemporaryDirectory() as temp_dir:
                root = Path(temp_dir)
                run_dir = copy_fixture(root, FIXTURE)
                attach_v3_allocation_run(run_dir, root / "v3")
                manifest_path = run_dir / "study-manifest.json"
                manifest = read_json(manifest_path)
                manifest["outputs"]["audience_allocation_jobs"][stage] = {
                    "status": "not_dispatched"
                }
                write_json(manifest_path, manifest)
                with self.assertRaisesRegex(
                    DashboardInputError, f"{label}.*not_dispatched"
                ):
                    render_to(run_dir, root / "dashboard.html")

    def test_accepted_v3_responses_must_reconcile_to_manifest_bound_jobs(
        self,
    ) -> None:
        def mutate_responses(run_dir: Path, mutate) -> None:
            response_path = run_dir / "panelist-responses.jsonl"
            responses = [
                json.loads(line)
                for line in response_path.read_text(encoding="utf-8").splitlines()
                if line
            ]
            mutate(responses, run_dir)
            response_path.write_text(
                "".join(
                    json.dumps(response, separators=(",", ":")) + "\n"
                    for response in responses
                ),
                encoding="utf-8",
            )

        def disjoint(responses: list[dict], _run_dir: Path) -> None:
            for response in responses:
                response["synthetic_replicate_id"] = (
                    f"disjoint-{response['synthetic_replicate_id']}"
                )

        def partial_overlap(responses: list[dict], _run_dir: Path) -> None:
            responses[0]["synthetic_replicate_id"] = (
                f"partial-{responses[0]['synthetic_replicate_id']}"
            )

        def coherent_substitution(responses: list[dict], run_dir: Path) -> None:
            response = responses[0]
            old_response_id = response["response_id"]
            response.update(
                {
                    "synthetic_replicate_id": "substituted-replicate",
                    "response_id": "substituted-response",
                    "reviewer_dispatch_id": "substituted-dispatch",
                }
            )
            feedback_path = run_dir / "feedback-synthesis.json"
            feedback = read_json(feedback_path)
            for theme in feedback["themes"]:
                theme["response_ids"] = [
                    response["response_id"]
                    if response_id == old_response_id
                    else response_id
                    for response_id in theme["response_ids"]
                ]
            write_json(feedback_path, feedback)

        cases = {
            "wholly_disjoint": disjoint,
            "partial_overlap": partial_overlap,
            "coherent_substitution": coherent_substitution,
        }
        for label, mutate in cases.items():
            with self.subTest(label=label), tempfile.TemporaryDirectory() as temp_dir:
                root = Path(temp_dir)
                run_dir = copy_fixture(root, FIXTURE)
                attach_v3_allocation_run(run_dir, root / "v3")
                mutate_responses(run_dir, mutate)
                with self.assertRaisesRegex(
                    DashboardInputError, "accepted response.*job|frozen job set"
                ):
                    render_to(run_dir, root / "dashboard.html")

    def test_accepted_v3_response_profile_identity_must_match_bound_job(
        self,
    ) -> None:
        for field in (
            "audience_slot_id",
            "grounded_profile_id",
            "profile_snapshot_sha256",
        ):
            with self.subTest(field=field), tempfile.TemporaryDirectory() as temp_dir:
                root = Path(temp_dir)
                run_dir = copy_fixture(root, FIXTURE)
                attach_v3_allocation_run(run_dir, root / "v3")
                response_path = run_dir / "panelist-responses.jsonl"
                responses = [
                    json.loads(line)
                    for line in response_path.read_text(encoding="utf-8").splitlines()
                    if line
                ]
                responses[0][field] = f"forged-{field}"
                response_path.write_text(
                    "".join(
                        json.dumps(response, separators=(",", ":")) + "\n"
                        for response in responses
                    ),
                    encoding="utf-8",
                )
                with self.assertRaisesRegex(
                    DashboardInputError, f"accepted response.*{field}"
                ):
                    render_to(run_dir, root / "dashboard.html")

    def test_standalone_validator_reconciles_v3_responses_to_bound_jobs(
        self,
    ) -> None:
        def rewrite_response_export(raw: bytes, label: str) -> bytes:
            responses = [
                json.loads(line)
                for line in raw.decode("utf-8").splitlines()
                if line
            ]
            if label == "wholly_disjoint":
                for response in responses:
                    response["synthetic_replicate_id"] = (
                        f"disjoint-{response['synthetic_replicate_id']}"
                    )
            elif label == "partial_overlap":
                responses[0]["synthetic_replicate_id"] = (
                    f"partial-{responses[0]['synthetic_replicate_id']}"
                )
            else:
                responses[0].update(
                    {
                        "synthetic_replicate_id": "substituted-replicate",
                        "response_id": "substituted-response",
                        "reviewer_dispatch_id": "substituted-dispatch",
                    }
                )
            return "".join(
                json.dumps(response, separators=(",", ":")) + "\n"
                for response in responses
            ).encode("utf-8")

        for label in (
            "wholly_disjoint",
            "partial_overlap",
            "coherent_substitution",
        ):
            with self.subTest(label=label), tempfile.TemporaryDirectory() as temp_dir:
                root = Path(temp_dir)
                run_dir = copy_fixture(root, FIXTURE)
                attach_v3_allocation_run(run_dir, root / "v3")
                output = root / "dashboard.html"
                render_to(run_dir, output)
                rewrite_dashboard_payload(
                    output,
                    lambda payload: replace_export_bytes(
                        payload,
                        "panelist-responses.jsonl",
                        lambda raw: rewrite_response_export(raw, label),
                    ),
                )
                result = validator_result(output)
                self.assertNotEqual(0, result.returncode)
                self.assertIn(
                    "accepted response job binding", result.stdout.lower()
                )

    def test_v3_dispatch_audit_must_reconcile_accepted_and_exhausted_jobs(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            run_dir = copy_fixture(root, FIXTURE)
            _plan, job_envelopes, _resolution = attach_v3_allocation_run(
                run_dir, root / "v3"
            )
            responses = [
                json.loads(line)
                for line in (run_dir / "panelist-responses.jsonl")
                .read_text(encoding="utf-8")
                .splitlines()
                if line
            ]
            response_by_dispatch = {
                response["reviewer_dispatch_id"]: response
                for response in responses
            }
            audit: list[dict] = []
            for envelope in job_envelopes.values():
                for job in envelope["synthetic_replicate_jobs"]:
                    response = response_by_dispatch.get(job["dispatch_id"])
                    accepted = response is not None
                    audit.append(
                        {
                            "record_type": job["record_type"],
                            "synthetic_replicate_id": job[
                                "synthetic_replicate_id"
                            ],
                            "reviewer_dispatch_id": job["dispatch_id"],
                            "accepted": accepted,
                            "attempt_contract": {
                                "retry_limit_per_return": 1,
                                "reaction_positions": list(
                                    range(1, len(job["shown_order"]) + 1)
                                ),
                                "comparison_required": True,
                            },
                            "reaction_attempts": (
                                [1] * len(job["shown_order"])
                                if accepted
                                else [2] * len(job["shown_order"])
                            ),
                            "comparison_attempts": 1 if accepted else 0,
                        }
                    )
            audit_path = run_dir / "dispatch-audit.jsonl"
            audit_path.write_text(
                "".join(
                    json.dumps(record, separators=(",", ":")) + "\n"
                    for record in audit
                ),
                encoding="utf-8",
            )
            valid_output = root / "valid-dashboard.html"
            render_to(run_dir, valid_output)

            def corrupt_audit_export(payload: dict) -> None:
                def toggle_first_record(raw: bytes) -> bytes:
                    records = [
                        json.loads(line)
                        for line in raw.decode("utf-8").splitlines()
                        if line
                    ]
                    records[0]["accepted"] = not records[0]["accepted"]
                    return "".join(
                        json.dumps(record, separators=(",", ":")) + "\n"
                        for record in records
                    ).encode("utf-8")

                replace_export_bytes(
                    payload, "dispatch-audit.jsonl", toggle_first_record
                )

            rewrite_dashboard_payload(valid_output, corrupt_audit_export)
            standalone_result = validator_result(valid_output)
            self.assertNotEqual(0, standalone_result.returncode)
            self.assertIn("dispatch audit", standalone_result.stdout.lower())

            audit[0]["accepted"] = not audit[0]["accepted"]
            audit_path.write_text(
                "".join(
                    json.dumps(record, separators=(",", ":")) + "\n"
                    for record in audit
                ),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(
                DashboardInputError, "dispatch audit.*accepted"
            ):
                render_to(run_dir, root / "invalid-dashboard.html")

    def test_complete_exposure_forbids_boundary_binding_or_file(self) -> None:
        for surface in ("binding", "file"):
            with self.subTest(surface=surface), tempfile.TemporaryDirectory() as temp_dir:
                root = Path(temp_dir)
                run_dir = copy_fixture(root, COPY_ONLY_FIXTURE)
                attach_v3_allocation_run(run_dir, root / "v3")
                if surface == "binding":
                    manifest_path = run_dir / "study-manifest.json"
                    manifest = read_json(manifest_path)
                    manifest["outputs"]["audience_allocation_jobs"][
                        "boundary"
                    ] = {"status": "dispatched", "waves": []}
                    write_json(manifest_path, manifest)
                else:
                    (run_dir / "boundary-wave-0001-jobs.json").write_bytes(
                        b"{}\n"
                    )
                with self.assertRaisesRegex(
                    DashboardInputError, "complete exposure.*boundary"
                ):
                    render_to(run_dir, root / "dashboard.html")

    def test_standalone_validator_rebinds_v3_jobs_and_displayed_diagnostics(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            run_dir = copy_fixture(root, FIXTURE)
            attach_v3_allocation_run(run_dir, root / "v3")
            output = root / "dashboard.html"
            render_to(run_dir, output)
            baseline = validator_result(output)
            self.assertEqual(
                0, baseline.returncode, baseline.stdout + baseline.stderr
            )
            rewrite_dashboard_payload(
                output,
                lambda payload: payload["audience"]["run_allocation"][
                    "stages"
                ][0]["diagnostics"][0]["structural_groups"][0].update(
                    raw_slot_share=0.999
                ),
            )
            result = validator_result(output)

        self.assertNotEqual(0, result.returncode)
        self.assertIn("allocation", result.stdout.lower())
        self.assertNotIn("canonical v3 dashboard shell", result.stdout.lower())

    def test_standalone_validator_requires_exact_v3_allocation_surfaces(
        self,
    ) -> None:
        mutations = {
            "section_removed": lambda html: html.replace(
                V3_ALLOCATION_SECTION_HTML, "", 1
            ),
            "renderer_removed": lambda html: html.replace(
                V3_ALLOCATION_SCRIPT, "", 1
            ),
            "renderer_substituted": lambda html: html.replace(
                V3_ALLOCATION_SCRIPT,
                V3_ALLOCATION_SCRIPT.replace(
                    "const allocationRoot", "let allocationRoot", 1
                ),
                1,
            ),
            "section_duplicated": lambda html: html.replace(
                V3_ALLOCATION_SECTION_HTML,
                V3_ALLOCATION_SECTION_HTML + V3_ALLOCATION_SECTION_HTML,
                1,
            ),
            "renderer_duplicated": lambda html: html.replace(
                V3_ALLOCATION_SCRIPT,
                V3_ALLOCATION_SCRIPT + V3_ALLOCATION_SCRIPT,
                1,
            ),
        }
        for label, mutate in mutations.items():
            with self.subTest(label=label), tempfile.TemporaryDirectory() as temp_dir:
                root = Path(temp_dir)
                run_dir = copy_fixture(root, FIXTURE)
                attach_v3_allocation_run(run_dir, root / "v3")
                output = root / "dashboard.html"
                render_to(run_dir, output)
                output.write_text(
                    mutate(output.read_text(encoding="utf-8")),
                    encoding="utf-8",
                )
                result = validator_result(output)
                self.assertNotEqual(
                    0,
                    result.returncode,
                    msg=f"{label}: {result.stdout}\n{result.stderr}",
                )

    def test_standalone_validator_requires_active_v3_allocation_surfaces(
        self,
    ) -> None:
        def html_commented_section(html: str) -> str:
            return html.replace(
                V3_ALLOCATION_SECTION_HTML,
                f"<!--{V3_ALLOCATION_SECTION_HTML}-->",
                1,
            )

        def js_block_commented_renderer(html: str) -> str:
            return html.replace(
                V3_ALLOCATION_SCRIPT,
                f"/*{V3_ALLOCATION_SCRIPT}*/",
                1,
            )

        def line_commented_renderer_with_inert_copy(html: str) -> str:
            line_commented = "\n".join(
                f"// {line}" for line in V3_ALLOCATION_SCRIPT.splitlines()
            )
            replaced = html.replace(V3_ALLOCATION_SCRIPT, line_commented, 1)
            return replaced.replace(
                "</body>",
                (
                    '<script type="text/plain" id="allocation-renderer-source">'
                    f"{V3_ALLOCATION_SCRIPT}</script></body>"
                ),
                1,
            )

        def renderer_moved_to_inert_string(html: str) -> str:
            replaced = html.replace(V3_ALLOCATION_SCRIPT, "", 1)
            return replaced.replace(
                "</body>",
                (
                    '<script type="text/plain" id="allocation-renderer-string">'
                    f"{V3_ALLOCATION_SCRIPT}</script></body>"
                ),
                1,
            )

        def dom_ids_moved_to_inert_string(html: str) -> str:
            return html.replace(
                V3_ALLOCATION_SECTION_HTML,
                (
                    '<script type="text/plain" id="allocation-section-string">'
                    f"{V3_ALLOCATION_SECTION_HTML}</script>"
                ),
                1,
            )

        mutations = {
            "html_commented_section": html_commented_section,
            "js_block_commented_renderer": js_block_commented_renderer,
            "line_commented_renderer": line_commented_renderer_with_inert_copy,
            "renderer_in_inert_string": renderer_moved_to_inert_string,
            "dom_ids_in_inert_string": dom_ids_moved_to_inert_string,
        }
        for label, mutate in mutations.items():
            with self.subTest(label=label), tempfile.TemporaryDirectory() as temp_dir:
                root = Path(temp_dir)
                run_dir = copy_fixture(root, FIXTURE)
                attach_v3_allocation_run(run_dir, root / "v3")
                output = root / "dashboard.html"
                render_to(run_dir, output)
                output.write_text(
                    mutate(output.read_text(encoding="utf-8")),
                    encoding="utf-8",
                )
                result = validator_result(output)
                self.assertNotEqual(
                    0,
                    result.returncode,
                    msg=f"{label}: {result.stdout}\n{result.stderr}",
                )

    def test_standalone_validator_rejects_inert_or_hidden_v3_surfaces(
        self,
    ) -> None:
        def wrap_section(html: str, wrapper: str) -> str:
            tag = wrapper.split(maxsplit=1)[0]
            return html.replace(
                V3_ALLOCATION_SECTION_HTML,
                f"<{wrapper}>{V3_ALLOCATION_SECTION_HTML}</{tag}>",
                1,
            )

        def wrap_runtime_script(html: str, wrapper: str) -> str:
            marker = "  <script>\n    (() => {"
            start = html.index(marker)
            closing = "\n  </script>"
            end = html.index(closing, start) + len(closing)
            tag = wrapper.split(maxsplit=1)[0]
            return (
                html[:start]
                + f"<{wrapper}>"
                + html[start:end]
                + f"</{tag}>"
                + html[end:]
            )

        def section_outside_body(html: str) -> str:
            without_section = html.replace(V3_ALLOCATION_SECTION_HTML, "", 1)
            return without_section.replace(
                "<body>",
                V3_ALLOCATION_SECTION_HTML + "\n<body>",
                1,
            )

        mutations = {
            "section_in_template": lambda html: wrap_section(html, "template"),
            "runtime_in_template": lambda html: wrap_runtime_script(
                html, "template"
            ),
            "section_in_noscript": lambda html: wrap_section(html, "noscript"),
            "runtime_in_noscript": lambda html: wrap_runtime_script(
                html, "noscript"
            ),
            "section_hidden": lambda html: wrap_section(html, "div hidden"),
            "section_inert": lambda html: wrap_section(html, "div inert"),
            "section_aria_hidden": lambda html: wrap_section(
                html, 'div aria-hidden="true"'
            ),
            "section_display_none": lambda html: wrap_section(
                html, 'div style="display:none"'
            ),
            "section_visibility_hidden": lambda html: wrap_section(
                html, 'div style="visibility: hidden"'
            ),
            "section_outside_body": section_outside_body,
        }
        for label, mutate in mutations.items():
            with self.subTest(label=label), tempfile.TemporaryDirectory() as temp_dir:
                root = Path(temp_dir)
                run_dir = copy_fixture(root, FIXTURE)
                attach_v3_allocation_run(run_dir, root / "v3")
                output = root / "dashboard.html"
                render_to(run_dir, output)
                output.write_text(
                    mutate(output.read_text(encoding="utf-8")),
                    encoding="utf-8",
                )
                result = validator_result(output)
                self.assertNotEqual(
                    0,
                    result.returncode,
                    msg=f"{label}: {result.stdout}\n{result.stderr}",
                )

    def test_standalone_validator_authenticates_entire_v3_shell(
        self,
    ) -> None:
        audience_tab = (
            '<section class="tab-panel" id="panel-audience" role="tabpanel" '
            'aria-labelledby="tab-audience" hidden>'
        )
        mutations = {
            "allocation_under_duplicate_canonical_hidden_tab": lambda html: (
                html.replace(
                    V3_ALLOCATION_SECTION_HTML,
                    (
                        audience_tab
                        + V3_ALLOCATION_SECTION_HTML
                        + "</section>"
                    ),
                    1,
                )
            ),
            "allocation_under_closed_details": lambda html: html.replace(
                V3_ALLOCATION_SECTION_HTML,
                f"<details>{V3_ALLOCATION_SECTION_HTML}</details>",
                1,
            ),
            "allocation_hidden_by_appended_stylesheet": lambda html: html.replace(
                "</head>",
                (
                    "<style>#audience-run-allocation { display:none }</style>"
                    "</head>"
                ),
                1,
            ),
            "shipped_logo_changed": lambda html: html.replace(
                'src="data:image/png;base64,',
                'src="data:image/png;base64,A',
                1,
            ),
            "shell_whitespace_changed": lambda html: html.replace(
                "<body>",
                "<body> \n",
                1,
            ),
        }
        for label, mutate in mutations.items():
            with self.subTest(label=label), tempfile.TemporaryDirectory() as temp_dir:
                root = Path(temp_dir)
                run_dir = copy_fixture(root, FIXTURE)
                attach_v3_allocation_run(run_dir, root / "v3")
                output = root / "dashboard.html"
                render_to(run_dir, output)
                mutated = mutate(output.read_text(encoding="utf-8"))
                self.assertNotEqual(
                    output.read_text(encoding="utf-8"),
                    mutated,
                    msg=f"{label} did not mutate the dashboard",
                )
                output.write_text(mutated, encoding="utf-8")
                result = validator_result(output)
                self.assertNotEqual(
                    0,
                    result.returncode,
                    msg=f"{label}: {result.stdout}\n{result.stderr}",
                )

    def test_standalone_validator_requires_canonical_surface_nesting_and_attrs(
        self,
    ) -> None:
        section_start = (
            '<section class="ledger-panel" id="audience-run-allocation">'
        )
        body_node = (
            '<div class="advanced-details-body" '
            'id="audience-run-allocation-body"></div>'
        )
        mutations = {
            "section_extra_attr": lambda html: html.replace(
                section_start,
                section_start[:-1] + ' data-forged="true">',
                1,
            ),
            "body_extra_attr": lambda html: html.replace(
                body_node,
                body_node.replace(
                    "></div>", ' data-forged="true"></div>'
                ),
                1,
            ),
            "body_outside_section": lambda html: html.replace(
                V3_ALLOCATION_SECTION_HTML,
                (
                    V3_ALLOCATION_SECTION_HTML.replace(body_node, "")
                    + body_node
                ),
                1,
            ),
            "runtime_script_extra_attr": lambda html: html.replace(
                "  <script>\n    (() => {",
                "  <script defer>\n    (() => {",
                1,
            ),
        }
        for label, mutate in mutations.items():
            with self.subTest(label=label), tempfile.TemporaryDirectory() as temp_dir:
                root = Path(temp_dir)
                run_dir = copy_fixture(root, FIXTURE)
                attach_v3_allocation_run(run_dir, root / "v3")
                output = root / "dashboard.html"
                render_to(run_dir, output)
                output.write_text(
                    mutate(output.read_text(encoding="utf-8")),
                    encoding="utf-8",
                )
                result = validator_result(output)
                self.assertNotEqual(
                    0,
                    result.returncode,
                    msg=f"{label}: {result.stdout}\n{result.stderr}",
                )

    def test_standalone_surface_contract_accepts_v3_modes_and_legacy(
        self,
    ) -> None:
        cases = (
            ("v3_partial", FIXTURE, True),
            ("v3_complete", COPY_ONLY_FIXTURE, True),
            ("legacy", FIXTURE, False),
        )
        for label, source, attach_v3 in cases:
            with self.subTest(label=label), tempfile.TemporaryDirectory() as temp_dir:
                root = Path(temp_dir)
                run_dir = copy_fixture(root, source)
                if attach_v3:
                    attach_v3_allocation_run(run_dir, root / "v3")
                output = root / "dashboard.html"
                render_to(run_dir, output)
                result = validator_result(output)
                self.assertEqual(
                    0,
                    result.returncode,
                    msg=f"{label}: {result.stdout}\n{result.stderr}",
                )

    def test_standalone_validator_forbids_v3_surfaces_without_v3_payload(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            run_dir = copy_fixture(root, FIXTURE)
            output = root / "dashboard.html"
            render_to(run_dir, output)
            html = output.read_text(encoding="utf-8")
            self.assertNotIn(V3_ALLOCATION_SECTION_HTML, html)
            self.assertNotIn(V3_ALLOCATION_SCRIPT, html)
            output.write_text(
                html + V3_ALLOCATION_SECTION_HTML + V3_ALLOCATION_SCRIPT,
                encoding="utf-8",
            )
            result = validator_result(output)
            self.assertNotEqual(
                0,
                result.returncode,
                msg=f"{result.stdout}\n{result.stderr}",
            )

    def test_v3_allocation_labels_round_trip_only_as_script_safe_text_data(
        self,
    ) -> None:
        hostile = "</script><img src=x onerror=globalThis.pwned=1>"
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            run_dir = copy_fixture(root, FIXTURE)
            attach_v3_allocation_run(run_dir, root / "v3")
            output = root / "dashboard.html"
            render_to(run_dir, output)

            def inject_hostile_allocation_labels(payload: dict) -> None:
                allocation = payload["audience"]["run_allocation"]
                allocation["package"]["panel_id"] = hostile
                allocation["package"]["panel_version"] = hostile
                allocation["reusable_weight_semantics"]["structural"][0] = hostile
                allocation["stages"][0]["diagnostics"][0]["structural_groups"][0][
                    "structural_group_id"
                ] = hostile

            rewrite_dashboard_payload(output, inject_hostile_allocation_labels)
            html = output.read_text(encoding="utf-8")
            allocation = payload_from(html)["audience"]["run_allocation"]

        self.assertEqual(hostile, allocation["package"]["panel_id"])
        self.assertEqual(hostile, allocation["package"]["panel_version"])
        self.assertEqual(
            hostile, allocation["reusable_weight_semantics"]["structural"][0]
        )
        self.assertEqual(
            hostile,
            allocation["stages"][0]["diagnostics"][0]["structural_groups"][0][
                "structural_group_id"
            ],
        )
        self.assertNotIn(hostile, html)
        self.assertIn(r"\u003c/script\u003e", html)
        self.assertIn(
            'addDataRow(semantics, "Reusable panel"', V3_ALLOCATION_SCRIPT
        )
        self.assertIn("make(", V3_ALLOCATION_SCRIPT)
        self.assertNotIn("innerHTML", V3_ALLOCATION_SCRIPT)

    def test_legacy_v1_and_v2_render_bytes_remain_exact(self) -> None:
        v1_partial = render_fixture_dashboard(FIXTURE).encode("utf-8")
        v1_complete = render_fixture_dashboard(COPY_ONLY_FIXTURE).encode(
            "utf-8"
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            run_dir = copy_fixture(Path(temp_dir), COPY_ONLY_FIXTURE)
            attach_research_backed_audience(run_dir)
            v2_complete = render_fixture_dashboard(run_dir).encode("utf-8")
        self.assertEqual(
            LEGACY_DASHBOARD_SHA256["v1_partial"],
            hashlib.sha256(v1_partial).hexdigest(),
        )
        self.assertEqual(
            LEGACY_DASHBOARD_SHA256["v1_complete"],
            hashlib.sha256(v1_complete).hexdigest(),
        )
        self.assertEqual(
            LEGACY_DASHBOARD_SHA256["v2_complete"],
            hashlib.sha256(v2_complete).hexdigest(),
        )


class DashboardAudiencePackageTests(unittest.TestCase):
    def test_rendered_audience_intro_binds_exact_payload_for_every_state(self):
        states = (
            ("legacy", None),
            ("research_backed", attach_research_backed_audience),
            ("provisional", attach_provisional_audience),
        )
        for expected_state, attach in states:
            with self.subTest(state=expected_state), tempfile.TemporaryDirectory() as temp_dir:
                run_dir = copy_fixture(Path(temp_dir), COPY_ONLY_FIXTURE)
                if attach is not None:
                    attach(run_dir)
                html = render_fixture_dashboard(run_dir)
                payload = payload_from(html)
                self.assertEqual(expected_state, payload["audience"]["state"])
                self.assertTrue(payload["audience"]["intro"])
                self.assertEqual(
                    1,
                    len(re.findall(r'<p class="section-intro" id="audience-intro"></p>', html)),
                )
                self.assertEqual(
                    1,
                    html.count(
                        'document.getElementById("audience-intro").textContent = '
                        "data.audience.intro;"
                    ),
                )
                self.assertNotRegex(
                    html,
                    r'document\.getElementById\("audience-intro"\)\.textContent\s*=\s*[`\'"]',
                )

    def test_v2_audience_comes_from_panel_with_hierarchy_and_primary_downloads(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            run_dir = copy_fixture(Path(temp_dir), COPY_ONLY_FIXTURE)
            attach_research_backed_audience(run_dir)
            html = render_fixture_dashboard(run_dir)
            payload = payload_from(html)

        self.assertEqual("research_backed", payload["audience"]["state"])
        self.assertEqual(
            "1 research-backed panelist profile represented 1 audience segment. "
            "Each profile combines a role, buyer mindset, and buying context.",
            payload["audience"]["intro"],
        )
        self.assertEqual("Operations Leaders", payload["audience"]["panel_name"])
        self.assertEqual("1.0.0", payload["audience"]["panel_version"])
        self.assertEqual("public_research", payload["audience"]["research_mode"])
        self.assertEqual(
            "Operations leaders at mid-market software companies",
            payload["audience"]["target_audience"],
        )
        self.assertEqual("Segment 1", payload["audience"]["segments"][0]["number_label"])
        self.assertEqual(
            "Evidence-led operations leaders",
            payload["audience"]["segments"][0]["name"],
        )
        self.assertEqual(
            "Evidence-led operator",
            payload["audience"]["segments"][0]["mindsets"][0]["name"],
        )
        self.assertEqual(1, payload["audience"]["panelist_profile_count"])
        self.assertEqual(1, len(payload["audience"]["panelist_profiles"]))
        self.assertEqual(
            "operations-director-evaluating-v1",
            payload["audience"]["panelist_profiles"][0]["profile_id"],
        )
        for text in (
            "Audience research and insights",
            "Audience research report",
            "Research sources for Excel",
            "Reusable AI audience panel",
            "Full audience package",
            "Panel version",
            "Research basis",
            "Research date",
        ):
            self.assertIn(text, html)
        self.assertIn(
            '<section class="ledger-panel audience-research-viewer" id="audience-research-viewer">',
            html,
        )
        self.assertNotIn(
            '<details class="advanced-details audience-research-viewer"',
            html,
        )
        exports = {item["filename"]: item for item in payload["exports"]}
        for filename in (
            "audience-research-report.html",
            "saved-audience-panel.json",
            "research-sources.csv",
        ):
            self.assertEqual("marketer", exports[filename]["audience"])
        for filename in ("persona-research-brief.json", "audience-panel-package.zip"):
            self.assertEqual("technical", exports[filename]["audience"])

    def test_v2_audience_rejects_loose_snapshot_or_binding_tampering(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            run_dir = copy_fixture(Path(temp_dir), COPY_ONLY_FIXTURE)
            attach_research_backed_audience(run_dir)
            panel_path = run_dir / "audience/snapshot/saved-audience-panel.json"
            panel_path.write_bytes(panel_path.read_bytes() + b" ")
            with self.assertRaisesRegex(DashboardInputError, "saved-audience-panel.json"):
                render_to(run_dir, Path(temp_dir) / "bad-panel.html")
        with tempfile.TemporaryDirectory() as temp_dir:
            run_dir = copy_fixture(Path(temp_dir), COPY_ONLY_FIXTURE)
            attach_research_backed_audience(run_dir)
            manifest_path = run_dir / "study-manifest.json"
            manifest = read_json(manifest_path)
            manifest["audience_package"]["panel_sha256"] = "0" * 64
            write_json(manifest_path, manifest)
            with self.assertRaisesRegex(DashboardInputError, "panel_sha256"):
                render_to(run_dir, Path(temp_dir) / "bad-binding.html")

    def test_legacy_audience_is_honest_and_omits_research_downloads(self):
        payload = payload_from(render_fixture_dashboard(COPY_ONLY_FIXTURE))
        self.assertEqual("legacy", payload["audience"]["state"])
        self.assertEqual(
            "Legacy panel metadata — audience research package unavailable",
            payload["audience"]["state_label"],
        )
        self.assertEqual(
            "The saved panelist profile definitions and audience research are "
            "unavailable for this legacy panel.",
            payload["audience"]["intro"],
        )
        self.assertNotIn("represented", payload["audience"]["intro"].lower())
        filenames = {item["filename"] for item in payload["exports"]}
        self.assertNotIn("audience-research-report.html", filenames)
        self.assertNotIn("research-sources.csv", filenames)
        self.assertNotIn("audience-panel-package.zip", filenames)

    def test_standalone_validator_rejects_tampered_embedded_audience_json(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            run_dir = copy_fixture(root, COPY_ONLY_FIXTURE)
            attach_research_backed_audience(run_dir)
            output = root / "dashboard.html"
            render_to(run_dir, output)
            baseline = validator_result(output)
            self.assertEqual(0, baseline.returncode, baseline.stdout + baseline.stderr)
            rewrite_dashboard_payload(
                output,
                lambda payload: replace_json_export(
                    payload,
                    "saved-audience-panel.json",
                    lambda panel: panel.update(panel_name="Tampered panel"),
                ),
            )
            result = validator_result(output)
        self.assertNotEqual(0, result.returncode)
        self.assertIn("audience", result.stdout.lower())

    def test_standalone_validator_binds_report_csv_and_package_bytes(self):
        for filename in (
            "audience-research-report.html",
            "research-sources.csv",
            "audience-panel-package.zip",
        ):
            with self.subTest(filename=filename), tempfile.TemporaryDirectory() as temp_dir:
                root = Path(temp_dir)
                run_dir = copy_fixture(root, COPY_ONLY_FIXTURE)
                attach_research_backed_audience(run_dir)
                output = root / "dashboard.html"
                render_to(run_dir, output)
                rewrite_dashboard_payload(
                    output,
                    lambda payload, name=filename: replace_export_bytes(
                        payload, name, lambda raw: raw + b"tampered"
                    ),
                )
                result = validator_result(output)
            self.assertNotEqual(0, result.returncode)
            self.assertIn("audience", result.stdout.lower())

    def test_provisional_audience_is_labeled_and_never_called_reusable(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            run_dir = copy_fixture(Path(temp_dir), COPY_ONLY_FIXTURE)
            attach_provisional_audience(run_dir)
            html = render_fixture_dashboard(run_dir)
            payload = payload_from(html)
        self.assertEqual("provisional", payload["audience"]["state"])
        self.assertIn(
            "provisional panelist profile",
            payload["audience"]["intro"],
        )
        self.assertIn("No research sources were used", payload["audience"]["intro"])
        self.assertIn("no research sources", payload["audience"]["state_label"].lower())
        package_export = next(
            item
            for item in payload["exports"]
            if item["filename"] == "audience-panel-package.zip"
        )
        self.assertEqual("Full audience package", package_export["label"])
        self.assertNotIn("Reusable audience package", html)
        sources = next(
            item for item in payload["exports"] if item["filename"] == "research-sources.csv"
        )
        raw, _ = _decode_test_data_url(sources["data_url"])
        self.assertEqual(1, len(raw.decode("utf-8").splitlines()))


class DashboardContractTests(unittest.TestCase):
    def test_rendered_dashboard_javascript_parses(self):
        node = shutil.which("node")
        if node is None:
            self.skipTest("Node.js is required for the dashboard JavaScript syntax check")
        html = render_fixture_dashboard()
        match = re.search(r"<script>\s*(.*?)\s*</script>", html, re.S)
        self.assertIsNotNone(match, "dashboard JavaScript block not found")
        with tempfile.TemporaryDirectory() as temp_dir:
            script_path = Path(temp_dir) / "dashboard.js"
            script_path.write_text(match.group(1), encoding="utf-8")
            result = subprocess.run(
                [node, "--check", str(script_path)],
                check=False,
                capture_output=True,
                text=True,
            )
        self.assertEqual(0, result.returncode, result.stderr)

    def test_dashboard_leads_with_marketer_language_and_keeps_audit_terms_secondary(self):
        html = render_fixture_dashboard()
        for label in (
            "Synthetic testing",
            "Creative test results",
            "What this test says",
            "The ads we tested",
            "How every ad performed",
            "Overall result",
            "How often it ranked among the leaders",
            "How the top ads compared",
            "Chosen first",
            "What synthetic panelists said",
            "AI audience responses",
            "Response table",
            "Full panelist records",
            "Download responses for Excel (.csv)",
            "How this test worked",
            "The basic idea",
            "What the results mean",
            "Download the source data",
        ):
            self.assertIn(label, html)
        self.assertNotIn('id="study-objective"', html)
        self.assertNotIn("Ready to use for this test decision", html)
        self.assertNotIn("How much to trust this read", html)
        self.assertIn("Conditional Within-Run Stability", html)
        self.assertIn("Conditional First-Choice Share", html)
        self.assertNotRegex(html, r"<h[1-3][^>]*>\s*Run validity and provenance")
        self.assertNotRegex(html, r"<h[1-3][^>]*>\s*Screening evidence")
        self.assertNotRegex(html, r"<h[1-3][^>]*>\s*Approved finalist evidence")
        self.assertNotRegex(html, r"<h[1-3][^>]*>\s*Feedback themes")
        self.assertNotIn("Confidence Rating", html)
        self.assertNotIn("Ranked Synthetic Preference", html)
        self.assertNotIn("specialist score", html.lower())

    def test_dashboard_explains_unfamiliar_labels_and_each_screening_result(self):
        html = render_fixture_dashboard()
        self.assertNotIn("Audience situations represented", html)
        self.assertNotIn("Context strata represented", html)
        self.assertIn('make("details", "info-popover")', html)
        self.assertIn('aria-label", `Explain ${label}`', html)
        self.assertIn("classificationWhy(item)", html)
        self.assertIn('scope="col">Review stages <span id="stage-help-slot"', html)
        self.assertIn('scope="col">Evidence <span id="key-evidence-help-slot"', html)
        self.assertIn("initial comparison could not separate it confidently", html)

    def test_info_popovers_close_on_escape_and_restore_trigger_focus(self):
        html = render_fixture_dashboard()
        self.assertIn('if (event.key !== "Escape" || !details.open) return;', html)
        self.assertIn("details.open = false;", html)
        self.assertIn("summary.focus();", html)

    def test_info_popovers_are_viewport_positioned_and_touch_accessible(self):
        html = render_fixture_dashboard()
        self.assertIn("position: fixed;", html)
        self.assertIn("window.innerWidth - bodyRect.width - margin", html)
        self.assertIn("window.innerHeight - margin", html)
        self.assertIn('@media (pointer: coarse)', html)
        self.assertIn("width: 2.75rem; height: 2.75rem;", html)

    def test_layout_uses_wide_canvas_and_reflows_methodology_for_tablets(self):
        html = render_fixture_dashboard()
        self.assertIn("width: min(100% - 2rem, 1600px);", html)
        self.assertNotIn("width: min(100% - 2rem, 1180px);", html)
        self.assertIn("repeat(4, minmax(15rem, 1fr))", html)
        self.assertIn("@media (max-width: 1100px)", html)
        self.assertIn(
            ".denominator-grid, .method-flow { grid-template-columns: repeat(2, minmax(0, 1fr)); }",
            html,
        )

    def test_typography_and_surfaces_avoid_heavy_ai_dashboard_styling(self):
        html = render_fixture_dashboard()
        self.assertNotIn('"Gill Sans"', html)
        self.assertIn('--display: "DM Serif Display", Georgia, serif;', html)
        self.assertIn('--sans: "Instrument Sans", system-ui', html)
        self.assertIn("border-radius: 6px;", html)
        self.assertNotIn("border-radius: 18px;", html)

    def test_overview_states_total_tested_and_finalist_counts(self):
        html = render_fixture_dashboard()
        self.assertIn('`${totalCount} tested · ${shortlistCount} ${approved ? "finalists" : "proposed"}`', html)
        self.assertIn('approved ? "Recommended next step" : "Decision pending"', html)
        self.assertIn('approved ? "Also compared" : "Also proposed"', html)
        self.assertIn("No finalist set recorded", html)

    def test_ads_tested_uses_an_uncropped_responsive_gallery(self):
        html = render_fixture_dashboard()
        self.assertIn("#creative-grid", html)
        self.assertIn("grid-template-columns: repeat(3, minmax(0, 1fr));", html)
        self.assertIn("align-items: stretch;", html)
        self.assertIn("#creative-grid .creative-card", html)
        self.assertIn("grid-template-rows: auto auto 1fr;", html)
        self.assertIn("#creative-grid .creative-media,", html)
        self.assertIn("aspect-ratio: 16 / 9;", html)
        self.assertIn(".creative-card.has-media { grid-column: auto; }", html)
        self.assertIn("height: auto;", html)
        self.assertNotIn("max-height: 430px;", html)
        self.assertIn('make("details", "creative-details")', html)

    def test_supporting_text_keeps_an_accessible_size_and_contrast_floor(self):
        html = render_fixture_dashboard()
        sizes = [
            float(value)
            for value in re.findall(r"font-size:\s*(0\.\d+)rem", html)
        ]
        undersized = [size for size in sizes if size < 0.75]
        self.assertEqual([], undersized)
        self.assertIn("--text-muted: #67677c;", html)
        self.assertIn(".study-meta dt { color: #b7b7c5; }", html)
        self.assertIn("color: #c2c2cf;", html)

    def test_navigation_preserves_full_width_canvas_without_decorative_bar_accents(self):
        html = render_fixture_dashboard()
        self.assertIn("flex-direction: row;", html)
        self.assertIn("overflow-x: auto;", html)
        self.assertNotIn("grid-template-columns: 13.5rem minmax(0, 1fr);", html)
        self.assertNotRegex(
            html,
            r"border-(?:left|right|top):\s*[2-9]px\s+solid",
        )
        self.assertNotIn(
            "linear-gradient(90deg, var(--royal) 0 5px, transparent 5px)",
            html,
        )

    def test_missing_media_uses_an_explicit_structured_concept_preview(self):
        html = render_fixture_dashboard()
        self.assertIn("function creativeConceptPreview(creative)", html)
        self.assertIn('const availability = hasDeclaredMedia ? "Image unavailable in dashboard" : "Image not supplied";', html)
        self.assertIn("`Concept preview · ${availability}`", html)
        self.assertIn('make("p", "concept-preview-body", creative.body)', html)
        self.assertIn('make("span", "concept-preview-cta", creative.cta)', html)
        self.assertIn("`Visual direction: ${creative.visual_description}`", html)
        self.assertIn("else card.append(creativeConceptPreview(creative));", html)

    def test_top_ad_results_include_thumbnails_and_explain_stage_denominators(self):
        html = render_fixture_dashboard()
        self.assertIn('creativeThumb(creativeId, "creative-media")', html)
        self.assertIn('const mediaStage = make("div", "finalist-media-stage");', html)
        self.assertIn(".finalist-media-stage > .creative-media", html)
        self.assertIn("aspect-ratio: 16 / 9;", html)
        self.assertNotIn(
            ".finalist-result-card > .creative-media { height: 100%;",
            html,
        )
        self.assertIn("complete aggregate result for one top ad", html)
        self.assertIn("additional closer-review records", html)
        self.assertNotIn("fresh closer-review AI profiles", html)
        self.assertIn("const closerBase = number(finalist.accepted_response_records)", html)
        self.assertIn("They were not shown prior answers", html)
        self.assertIn("not a human sample-size claim", html)

    def test_all_ad_results_does_not_reserve_thumbnail_space_without_media(self):
        html = render_fixture_dashboard()
        self.assertIn(
            'wrapper.classList.add(thumbnail ? "has-thumb" : "no-thumb");',
            html,
        )
        self.assertIn(".ad-with-thumb.has-thumb { display: grid;", html)
        self.assertIn(".ad-with-thumb.no-thumb { display: block; }", html)

    def test_feedback_is_rich_and_explicitly_synthetic(self):
        html = render_fixture_dashboard()
        for label in (
            "All verbatim AI feedback",
            "panelist reaction",
            "Noticed first",
            "What landed",
            "What raised questions",
            "What was noticed first",
            "What the test synthesized",
            "What to test next",
            "Why it matters:",
            "Recommended action:",
            "Evidence details",
            "Support:",
            "eligible accepted responses",
        ):
            self.assertIn(label, html)
        for useful_fixture_text in (
            "The delayed handoff is concrete and recognizable.",
            "Specific and credible.",
            "Keep the workflow review concrete.",
            '"attention_potential":4',
        ):
            self.assertIn(useful_fixture_text, html)
        self.assertNotIn("selectRepresentativeQuotes", html)
        self.assertIn("completeQuotes.forEach", html)
        self.assertIn("syntheticQuote", html)
        self.assertIn("groupFeedbackThemes", html)
        self.assertIn("item.grouped_sources.flatMap", html)
        self.assertIn("Main-test panelist reaction", html)
        self.assertIn("Closer-review panelist reaction", html)
        self.assertIn("Every accepted synthetic panelist supplied ad-specific written feedback", html)
        self.assertIn("AI audience responses provides the compact table view", html)
        self.assertIn("does not demonstrate production response diversity", html)
        self.assertIn('id="feedback-creative-filter"', html)
        self.assertIn('id="feedback-filter-status"', html)
        self.assertIn("Show feedback for", html)
        self.assertIn("applyFeedbackFilter", html)
        self.assertIn("entry.card.hidden = entry !== selected", html)
        self.assertIn("ad responses in the table", html)
        self.assertIn('document.getElementById("tab-responses").click()', html)
        self.assertIn("Object.entries(item.rubric_scores || {})", html)
        payload = payload_from(html)
        first_theme = payload["feedback"][0]
        self.assertFalse(payload["study"]["is_deterministic_fixture"])
        self.assertEqual("strength", first_theme["feedback_type"])
        self.assertIn("marketer", first_theme["why_it_matters"].lower())
        self.assertIn("test", first_theme["recommended_action"].lower())
        self.assertTrue(first_theme["limitations"])
        self.assertNotIn('synthesis.append(make("p", "", `Limitations:', html)
        self.assertIn('make("details", "theme-details")', html)
        self.assertIn('`Limits: ${limitations.join(" ")}`', html)

    def test_deterministic_fixture_provider_returns_are_labeled(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            run_dir = Path(temp_dir)
            (run_dir / "raw-provider-returns.jsonl").write_text(
                json.dumps(
                    {
                        "raw_return": {
                            "fixture": "deterministic conformance provider return"
                        }
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            self.assertTrue(_is_deterministic_conformance_fixture(run_dir))
        html = render_fixture_dashboard()
        self.assertIn("Deterministic test fixture audience", html)
        self.assertIn("Fictional conformance inputs", html)
        self.assertIn(
            "No substantive audience research was conducted, so no audience research report is shown.",
            html,
        )
        self.assertIn("isFixtureResearchFile", html)

    def test_all_ad_results_compares_all_ads_scores_and_retest_stages(self):
        html = render_fixture_dashboard()
        header = re.search(
            r'<table id="screening-table">.*?<thead>\s*<tr>(.*?)</tr>\s*</thead>',
            html,
            re.S,
        )
        self.assertIsNotNone(header)
        self.assertEqual(10, header.group(1).count('<th scope="col">'))
        for label in (
            "Ad",
            "Main-test result",
            "Review stages",
            "Overall",
            "Comprehension",
            "Relevance",
            "Credibility",
            "Offer appeal",
            "Motivation",
            "Evidence",
        ):
            self.assertIn(label, header.group(1))
        self.assertIn("#screening-table th:nth-child(10)", html)
        self.assertIn("min-width: 19rem", html)
        self.assertIn("reviewStagesForCreative", html)
        self.assertIn("reviewStageBadges", html)
        self.assertIn("closerScoreCell", html)
        self.assertIn("did not reach the closer review", html)
        self.assertIn("data.screening.boundary.boundary_candidate_ids", html)
        self.assertIn('"Additional AI reviews"', html)
        self.assertIn('"Ad advanced"', html)
        self.assertIn('make("details", "screening-details")', html)
        self.assertIn("View explanation and counts", html)
        screening = payload_from(html)["screening"]
        self.assertEqual("Overall result", screening["estimand_primary_label"])
        self.assertEqual(
            "How often it ranked among the leaders", screening["primary_label"]
        )
        self.assertNotEqual("First-round signal", screening["estimand_primary_label"])
        self.assertNotEqual("How often it stayed in the cut", screening["primary_label"])
        for detail in (
            "Times shown",
            "Picked strongest",
            "Picked weakest",
            "No clear difference",
            "Could not judge",
        ):
            self.assertIn(f'addDataRow(ledger, "{detail}"', html)

    def test_wide_comparison_tables_pin_reference_columns_responsively(self):
        html = render_fixture_dashboard()
        self.assertIn("#screening-table th:nth-child(1),", html)
        self.assertIn("left: 14rem;", html)
        self.assertIn(".response-table th:nth-child(1),", html)
        self.assertIn(".response-table td:nth-child(2) {", html)
        self.assertIn("box-shadow: 10px 0 14px -14px rgba(17, 17, 17, 0.42);", html)
        self.assertIn("#screening-table .table-thumb { width: 3rem; }", html)
        self.assertIn("width: 13rem;", html)
        self.assertIn("isolation: isolate;", html)

    def test_synthetic_limits_are_not_repeated_across_marketer_views(self):
        html = render_fixture_dashboard()
        for repeated_phrase in (
            "not customer feedback",
            "not customer quotes",
            "does not represent customer or market preference",
        ):
            self.assertNotIn(repeated_phrase, html.lower())
        self.assertEqual(1, html.lower().count("not customer survey results"))
        self.assertNotIn("AI-generated evidence", html)
        self.assertIn("Synthetic testing", html)

    def test_all_verbatim_reactions_are_default_and_panelist_detail_is_secondary(self):
        html = render_fixture_dashboard()
        self.assertIn('id="show-response-table" type="button" aria-pressed="true"', html)
        self.assertIn('id="show-response-details" type="button" aria-pressed="false"', html)
        self.assertIn('id="response-list" hidden', html)
        for heading in (
            "Ad",
            "Verbatim reaction",
            "Noticed first",
            "What worked",
            "What raised questions",
            "Closer-review note",
            "Comparison rationale",
        ):
            self.assertIn(f'<th scope="col">{heading}</th>', html)
        self.assertIn("const reactionRows", html)
        self.assertIn("verbatim ad reactions from", html)
        self.assertIn('"Verbatim reaction", reaction.immediate_reaction', html)
        self.assertIn('"What worked", reaction.positive_signal', html)
        self.assertIn('"What raised questions", reaction.negative_signal', html)

    def test_methodology_leads_with_plain_language_and_keeps_explanation_open(self):
        html = render_fixture_dashboard()
        self.assertIn("creates synthetic panelists from evidence-grounded buyer profiles", html)
        self.assertIn("Why this method was used", html)
        self.assertIn("compares all ads together for 2–6 ads", html)
        self.assertIn("balanced four-ad groups for 7 or more", html)
        self.assertIn("predeclared wave size, not a universal sample-size rule", html)
        self.assertIn("What each synthetic panelist was asked", html)
        self.assertIn("This comparison rationale is separate from the verbatim ad reactions", html)
        self.assertIn("Why the later rounds used new panelists", html)
        self.assertIn("same-profile reliability check", html)
        self.assertIn("Technical audit resources", html)
        self.assertIn("Technical audit details", html)
        self.assertIn("Study integrity", html)
        self.assertIn("What was checked in this run", html)
        self.assertIn('id="method-integrity-summary"', html)
        self.assertIn("Open the technical audit trail", html)
        self.assertIn("No recorded study-integrity evidence was supplied", html)
        self.assertNotIn("Recorded quality checks", html)
        self.assertIn('make("article", "integrity-row")', html)
        self.assertIn("See recorded evidence", html)
        self.assertNotIn("<summary>Technical definitions and quality checks</summary>", html)
        self.assertNotIn("<summary>Technical audit details</summary>", html)
        self.assertIn("panelist-responses.jsonl", html)
        self.assertIn("raw-provider-returns.jsonl", html)
        self.assertIn("dispatch-audit.jsonl", html)
        self.assertNotIn("Run integrity dimensions", html)

    def test_dashboard_displays_plain_language_evidence_counts(self):
        html = render_fixture_dashboard()
        self.assertIn("Synthetic panelists", html)
        self.assertIn("Audience profiles", html)
        self.assertIn("Synthetic panelists by test stage", html)
        self.assertIn(
            '<section class="overview-method-details" aria-labelledby="summary-participation-summary">',
            html,
        )
        self.assertNotIn('<details class="overview-method-details">', html)
        self.assertIn("Main-test synthetic panelists", html)
        self.assertIn("Tie-break synthetic panelists", html)
        self.assertIn("Closer-review synthetic panelists", html)
        self.assertIn("unique synthetic panelists participated across the complete run", html)
        self.assertNotIn("not human research participants", html)
        self.assertNotIn("not human respondents", html)
        self.assertIn("mainPanelBySegment", html)
        self.assertIn('make("span", "denominator-help", help)', html)
        self.assertNotIn("AI panelist runs", html)
        self.assertNotIn("Panelists total", html)
        self.assertIn("totalSyntheticPanelists", html)
        self.assertNotIn("participant-count-card", html)
        self.assertLess(
            html.index('class="overview-decision-grid"'),
            html.index('class="overview-stat-strip"'),
        )
        for removed_row in (
            'addDataRow(blocks, "Total model calls"',
            'addDataRow(blocks, "Accepted synthetic replicates"',
            'addDataRow(blocks, "Accepted records by test round"',
            'addDataRow(blocks, "Accepted replicates by test round"',
        ):
            self.assertNotIn(removed_row, html)
        self.assertNotIn("Audience situations represented", html)

    def test_dashboard_has_exact_core_tabs(self):
        html = render_fixture_dashboard()
        for label in (
            "Overview",
            "Ads tested",
            "Test audience",
            "All ad results",
            "Top ads",
            "Feedback",
            "AI audience responses",
            "Methodology",
            "Downloads",
        ):
            self.assertRegex(html, rf'<button[^>]+role="tab"[^>]*>{label}</button>')

    def test_dashboard_shows_panelist_profiles_without_duplicate_review_job_roster(self):
        html = render_fixture_dashboard()
        self.assertNotIn("AI profile variations", html)
        self.assertNotIn("Accepted unique synthetic replicates", html)
        self.assertIn('id="audience-who"', html)
        self.assertIn('id="audience-summary-grid"', html)
        self.assertNotIn('id="audience-table"', html)
        self.assertIn("Who the test represented", html)
        self.assertIn("Audience tested", html)
        self.assertIn("Target buyer", html)
        self.assertIn("Industry", html)
        self.assertIn("Organization type", html)
        self.assertIn("Job functions", html)
        self.assertIn("Seniority", html)
        self.assertIn("Panelist profile", html)
        self.assertIn("reusable modeled panelists", html)
        self.assertIn("Decision context", html)
        self.assertIn("This run did not record the target buyer’s industry", html)
        self.assertIn("researched buyer mindsets", html)
        self.assertIn("Profile source details", html)
        self.assertIn("data.audience.panelist_profiles", html)
        self.assertNotIn("View the complete AI profile roster", html)
        self.assertNotIn("Test rounds", html)
        self.assertIn("renderAudience();", html)

    def test_top_ads_owns_single_score_comparison_table(self):
        html = render_fixture_dashboard()
        table = re.search(
            r'<table class="score-comparison-table" id="finalist-score-table">.*?<thead>\s*<tr>(.*?)</tr>',
            html,
            re.S,
        )
        self.assertIsNotNone(table)
        self.assertEqual(7, table.group(1).count('<th scope="col">'))
        for label in (
            "Ad",
            "Overall",
            "Comprehension",
            "Relevance",
            "Credibility",
            "Offer appeal",
            "Motivation",
        ):
            self.assertIn(label, table.group(1))
        self.assertIn("How the top ads scored", html)
        self.assertIn("closerScoreDimensions", html)
        self.assertIn('["comprehension", "Comprehension"]', html)
        self.assertIn('["motivation", "Motivation"]', html)
        self.assertNotIn('id="overview-score-table"', html)
        self.assertNotIn('id="summary-scorecard"', html)
        self.assertIn('id="summary-score-matrix"', html)
        self.assertIn('const matrixDimensions = [', html)
        self.assertIn('["attention_potential", "Atten."]', html)
        self.assertIn('["overall", "Overall"]', html)
        self.assertIn('const aliases = field === "overall" ? ["overall", "overall_response"] : [field];', html)
        self.assertIn('const overall = finalistScoreMean(creativeId, "overall");', html)
        self.assertIn('!["overall", "overall_response"].includes(name)', html)
        self.assertGreater(
            html.index('id="finalist-scorecard"'),
            html.index('id="panel-finalists"'),
        )

    def test_overview_uses_responsive_decision_grid_visual_system(self):
        html = render_fixture_dashboard()
        for surface_id in (
            "summary-shortlist",
            "summary-trust",
            "summary-choice-share",
            "summary-score-matrix",
            "summary-rank-movement",
            "summary-first-round",
        ):
            self.assertIn(f'id="{surface_id}"', html)
        self.assertIn("--royal: #4f46e5;", html)
        self.assertIn("--signal: #ccfbf1;", html)
        self.assertIn("--trust: #ccfbf1;", html)
        self.assertIn("--trust-strong: #101014;", html)
        self.assertNotIn("#0f766e", html.lower())
        self.assertNotIn("#dfff6b", html.lower())
        self.assertIn("grid-template-columns: repeat(12, minmax(0, 1fr));", html)
        self.assertIn("@media (max-width: 1100px)", html)
        self.assertIn("@media (max-width: 760px)", html)
        self.assertIn('summary.validity_status === "valid"', html)
        self.assertIn("Closer-review scores will appear after approval.", html)
        self.assertIn("No approved closer-review result is available yet.", html)

    def test_information_rich_tabs_share_overview_system_without_architecture_changes(self):
        html = render_fixture_dashboard()
        for panel_id in (
            "panel-creatives",
            "panel-audience",
            "panel-screening",
            "panel-finalists",
            "panel-feedback",
            "panel-responses",
            "panel-methodology",
            "panel-exports",
        ):
            self.assertIn(f'id="{panel_id}"', html)
            self.assertIn(f"#{panel_id}", html)
        self.assertIn("#panel-attention-heatmap", html)
        self.assertIn(
            ".tab-panel:not(#panel-summary) > .section-header", html
        )
        self.assertIn("background: transparent;", html)
        self.assertIn("#panel-creatives .creative-card", html)
        self.assertIn("#panel-screening th", html)
        self.assertIn("#panel-feedback .feedback-filter-bar", html)
        self.assertIn("#panel-responses .response-roster-summary", html)
        self.assertIn("#panel-methodology .method-step:nth-child(even)", html)
        self.assertIn("#panel-exports .export-link", html)
        self.assertIn("#panel-exports .export-link:hover", html)
        self.assertNotIn("0.28rem solid var(--royal)", html)
        self.assertNotIn("0.2rem solid var(--trust)", html)
        self.assertNotIn("top / 100% 0.28rem no-repeat", html)
        self.assertNotIn("overflow: clip;", html)
        self.assertIn("overflow: visible;", html)
        self.assertNotIn(
            "linear-gradient(90deg, var(--royal) 0 68%, var(--trust) 68% 100%)",
            html,
        )
        self.assertNotIn("border-left: 0.34rem solid var(--royal)", html)
        self.assertNotIn("border-left: 0.3rem solid var(--royal)", html)

    def test_overview_omits_redundant_permission_and_next_action_box(self):
        html = render_fixture_dashboard()
        self.assertNotIn('id="summary-validity"', html)
        self.assertNotIn("What to do next", html)
        self.assertNotIn("Can I use these results?", html)

    def test_response_reaction_columns_keep_readable_width_and_scroll(self):
        html = render_fixture_dashboard()
        self.assertIn('replaceAll(/[_-]/g, " ")', html)
        self.assertIn(".response-table { min-width: 2260px;", html)
        self.assertIn(".response-table th:nth-child(5), .response-table td:nth-child(5) { min-width: 22rem; }", html)
        self.assertIn("overflow-x: auto;", html)
        self.assertIn("entry.immediate_reaction", html)

    def test_dashboard_embeds_official_innovaition_partners_branding(self):
        html = render_fixture_dashboard()
        self.assertIn('alt="InnovAItion Partners"', html)
        self.assertIn("data:image/png;base64,", html)
        self.assertNotIn("__IP_LOGO_DATA_URL__", html)
        self.assertIn("--ledger-ink: #111111;", html)
        self.assertIn("--paper-blue: #ffffff;", html)
        self.assertIn("--valid-teal: #0000ee;", html)
        self.assertIn("--surface-muted: #f1f4ff;", html)
        self.assertIn("--line-strong: #93b4ff;", html)

    def test_response_filters_cover_every_required_dimension(self):
        html = render_fixture_dashboard()
        for filter_name in (
            "stage",
            "synthetic-profile",
            "segment",
            "archetype",
            "creative",
            "best",
            "weakest",
            "tie",
            "unable-to-judge",
        ):
            self.assertIn(f'data-filter="{filter_name}"', html)
        for label in (
            "Test round",
            "Synthetic panelist ID",
            "Audience segment",
            "Buyer mindset",
            "Ad",
            "Picked strongest",
            "Picked weakest",
            "No clear difference",
            "Could not judge",
        ):
            self.assertIn(label, html)

    def test_mobile_response_filters_prioritize_round_and_ad(self):
        html = render_fixture_dashboard()
        primary = re.search(
            r'<div class="filter-bar response-filter-primary".*?</div>\s*</div>',
            html,
            re.S,
        )
        self.assertIsNotNone(primary)
        self.assertIn('id="filter-stage"', primary.group(0))
        self.assertIn('id="filter-creative"', primary.group(0))
        self.assertNotIn('id="filter-profile"', primary.group(0))
        self.assertIn(
            '<details class="response-filter-more" id="response-filter-more" open>',
            html,
        )
        self.assertIn('id="response-filter-count">No additional filters', html)
        self.assertIn('window.matchMedia("(max-width: 620px)").matches', html)
        self.assertIn("additionalFilterPanel.open = false;", html)
        self.assertIn("selectedAdditionalFilters", html)
        self.assertIn("additional filter", html)
        self.assertIn("min-height: 2.75rem; padding-block: 0.35rem;", html)

    def test_response_roster_reconciles_panelists_by_stage_and_segment(self):
        html = render_fixture_dashboard()
        for label in (
            "Who participated in this run",
            "synthetic panelists participated",
            "Main test",
            "Tie-break",
            "Closer review",
            "Only accepted synthetic panelists appear here",
        ):
            self.assertIn(label, html)
        self.assertGreater(
            html.index('class="response-roster-summary"'),
            html.index('id="response-table-wrap"'),
        )
        self.assertIn('id="response-roster-total"', html)
        self.assertIn('id="response-stage-grid"', html)
        self.assertIn("const uniquePanelists", html)
        self.assertIn("const segmentCounts = new Map()", html)
        self.assertIn('addOptions(document.getElementById("filter-profile"), responses, "synthetic_profile_id", "synthetic_profile_id"', html)
        self.assertIn("item.synthetic_profile_id,", html)

    def test_screening_never_claims_partial_exposure_is_a_like_share(self):
        html = render_fixture_dashboard()
        self.assertNotRegex(html, r"(?i)\b\d+(?:\.\d+)?%\s+liked\s+it\b")
        self.assertNotRegex(
            html,
            r"(?i)(?:estimates?|measures?|represents?)\s+(?:the\s+)?population preference",
        )
        self.assertNotIn("population preference share", html.lower())
        self.assertNotIn("market share", html.lower())
        self.assertIn("protocol-relative", html.lower())

    def test_one_parseable_payload_is_deterministic(self):
        first = render_fixture_dashboard()
        second = render_fixture_dashboard()
        self.assertEqual(first, second)
        self.assertEqual(1, len(PAYLOAD_RE.findall(first)))
        payload = payload_from(first)
        self.assertEqual("dashboard-acme-001", payload["study"]["study_id"])
        self.assertEqual("exploratory", payload["summary"]["validity_status"])
        self.assertEqual("Directional only", payload["summary"]["validity_label"])
        self.assertTrue(payload["summary"]["roster_decision"]["override"])


class DashboardSafetyTests(unittest.TestCase):
    def test_user_text_is_script_safe_and_round_trips(self):
        attack = '</script><img src=x onerror="alert(1)"><b>unsafe & text</b>'
        with tempfile.TemporaryDirectory() as temp_dir:
            run_dir = copy_fixture(Path(temp_dir))
            roster_path = run_dir / "creative-roster.json"
            roster = json.loads(roster_path.read_text(encoding="utf-8"))
            roster["creatives"][0]["display_name"] = attack
            roster_path.write_text(json.dumps(roster), encoding="utf-8")
            output = Path(temp_dir) / "dashboard.html"
            render_dashboard(
                run_dir,
                SKILL_ROOT / "assets" / "dashboard-template.html",
                output,
            )
            html = output.read_text(encoding="utf-8")

        self.assertNotIn(attack, html)
        self.assertNotIn("<img src=x", html)
        self.assertIn(r"\u003c/script\u003e", html)
        self.assertEqual(attack, payload_from(html)["creatives"][0]["display_name"])

    def test_local_media_is_embedded_and_no_runtime_fetch_remains(self):
        html = render_fixture_dashboard(include_saliency=True)
        payload = payload_from(html)
        creative_media = payload["creatives"][0]["media"][0]
        second_creative_media = payload["creatives"][1]["media"][0]
        evidence = payload["visual_evidence"]["entries"][0]
        second_evidence = payload["visual_evidence"]["entries"][1]
        self.assertTrue(creative_media["data_url"].startswith("data:image/svg+xml;base64,"))
        self.assertTrue(second_creative_media["data_url"].startswith("data:image/svg+xml;base64,"))
        self.assertTrue(evidence["original_data_url"].startswith("data:image/svg+xml;base64,"))
        self.assertTrue(evidence["overlay_data_url"].startswith("data:image/svg+xml;base64,"))
        self.assertTrue(second_evidence["original_data_url"].startswith("data:image/svg+xml;base64,"))
        self.assertTrue(second_evidence["overlay_data_url"].startswith("data:image/svg+xml;base64,"))
        self.assertNotIn("fetch(", html)
        self.assertNotIn("XMLHttpRequest", html)
        self.assertNotIn("file://", html)
        self.assertNotRegex(html, r'(?i)(?:src|href)="https?://')

    def test_design_review_fixture_gives_every_finalist_representative_media(self):
        payload = payload_from(render_fixture_dashboard())
        creative_by_id = {
            creative["variation_id"]: creative for creative in payload["creatives"]
        }
        finalist_ids = {
            item["variation_id"] for item in payload["summary"]["ads_moving_forward"]
        }
        self.assertEqual({"creative-a", "creative-b"}, finalist_ids)
        for creative_id in finalist_ids:
            with self.subTest(creative_id=creative_id):
                self.assertTrue(creative_by_id[creative_id]["media"])
                self.assertTrue(
                    all(
                        medium["data_url"].startswith("data:image/")
                        for medium in creative_by_id[creative_id]["media"]
                    )
                )

    def test_specialist_scores_are_not_carried_into_dashboard_data(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            run_dir = copy_fixture(Path(temp_dir))
            feedback_path = run_dir / "feedback-synthesis.json"
            feedback = json.loads(feedback_path.read_text(encoding="utf-8"))
            feedback["creative_specialist_scores"] = {"creative-a": 99}
            feedback_path.write_text(json.dumps(feedback), encoding="utf-8")
            output = Path(temp_dir) / "dashboard.html"
            render_dashboard(
                run_dir,
                SKILL_ROOT / "assets" / "dashboard-template.html",
                output,
            )
            html = output.read_text(encoding="utf-8")

        self.assertNotIn("creative_specialist_scores", html)
        self.assertNotIn("specialist score", html.lower())

    def test_path_traversal_media_is_rejected(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            run_dir = copy_fixture(root)
            roster_path = run_dir / "creative-roster.json"
            roster = json.loads(roster_path.read_text(encoding="utf-8"))
            roster["creatives"][0]["media"][0]["path"] = "../secret.svg"
            roster_path.write_text(json.dumps(roster), encoding="utf-8")
            (root / "secret.svg").write_text("<svg/>", encoding="utf-8")
            with self.assertRaisesRegex(DashboardInputError, "must stay inside run directory"):
                render_dashboard(
                    run_dir,
                    SKILL_ROOT / "assets" / "dashboard-template.html",
                    root / "dashboard.html",
                )


class DashboardSaliencyTests(unittest.TestCase):
    def test_imagery_automatically_includes_attention_heatmap(self):
        html = render_fixture_dashboard(include_saliency=False)
        payload = payload_from(html)
        self.assertTrue(payload["study"]["imagery_expected"])
        self.assertIsNotNone(payload["visual_evidence"])
        self.assertTrue(payload["summary"]["attention_heatmap_available"])
        self.assertIn('"Attention heatmap"', html)
        self.assertIn('"Where attention is likely to go"', html)

    def test_copy_only_run_omits_heatmap_and_says_no_imagery_was_tested(self):
        html = render_fixture_dashboard(COPY_ONLY_FIXTURE)
        payload = payload_from(html)
        self.assertFalse(payload["study"]["imagery_expected"])
        self.assertIsNone(payload["visual_evidence"])
        self.assertFalse(payload["summary"]["attention_heatmap_available"])
        self.assertEqual(
            "No imagery was tested.",
            payload["methodology"]["visual_attention_status"],
        )

    def test_visual_evidence_is_available_only_after_roster_approval(self):
        html = render_fixture_dashboard()
        payload = payload_from(html)
        self.assertTrue(payload["visual_evidence"]["roster_approved_before_reveal"])
        self.assertEqual("SUM", payload["visual_evidence"]["provider"])
        self.assertIn("Proof point and request-demo CTA", html)

    def test_unapproved_roster_fails_instead_of_suppressing_required_heatmap(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            run_dir = copy_fixture(Path(temp_dir))
            finalist_path = run_dir / "finalist-results.json"
            finalists = json.loads(finalist_path.read_text(encoding="utf-8"))
            finalists["roster_decision"]["status"] = "awaiting_approval"
            finalists["roster_decision"]["override"] = False
            finalist_path.write_text(json.dumps(finalists), encoding="utf-8")
            with self.assertRaisesRegex(DashboardInputError, "roster approval"):
                render_dashboard(
                    run_dir,
                    SKILL_ROOT / "assets" / "dashboard-template.html",
                    Path(temp_dir) / "dashboard.html",
                )

    def test_imagery_without_saliency_fails_explicitly(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            run_dir = copy_fixture(Path(temp_dir))
            (run_dir / "saliency-index.json").unlink()
            with self.assertRaisesRegex(
                DashboardInputError, "requires saliency-index.json attention evidence"
            ):
                render_dashboard(
                    run_dir,
                    SKILL_ROOT / "assets" / "dashboard-template.html",
                    Path(temp_dir) / "dashboard.html",
                )

    def test_each_creative_with_inspectable_media_requires_a_heatmap_entry(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            run_dir = copy_fixture(Path(temp_dir))
            roster_path = run_dir / "creative-roster.json"
            roster = json.loads(roster_path.read_text(encoding="utf-8"))
            roster["creatives"][2]["media"] = [
                {
                    "representation_id": "creative-c-static-01",
                    "content_hash": "sha256:817a3c9edf59e87254983c2f2234554b4280836903a6e8ff79c23690899491ed",
                    "kind": "image",
                    "path": "media/creative-a.svg",
                    "alt": "Additional inspectable ad",
                }
            ]
            roster_path.write_text(json.dumps(roster), encoding="utf-8")
            with self.assertRaisesRegex(
                DashboardInputError, "missing attention heatmap entries.*creative-c-static-01"
            ):
                render_dashboard(
                    run_dir,
                    SKILL_ROOT / "assets" / "dashboard-template.html",
                    Path(temp_dir) / "dashboard.html",
                )

    def test_compatibility_flag_cannot_suppress_required_heatmap(self):
        without_flag = payload_from(render_fixture_dashboard(include_saliency=False))
        with_flag = payload_from(render_fixture_dashboard(include_saliency=True))
        self.assertEqual(without_flag["visual_evidence"], with_flag["visual_evidence"])

    def test_every_supported_imagery_format_requires_the_heatmap(self):
        for creative_format in (
            "static_image",
            "carousel",
            "video_representation",
        ):
            with self.subTest(creative_format=creative_format), tempfile.TemporaryDirectory() as temp_dir:
                run_dir = copy_fixture(Path(temp_dir))
                manifest_path = run_dir / "study-manifest.json"
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                manifest["creative_format"] = creative_format
                manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
                output = Path(temp_dir) / "dashboard.html"
                render_dashboard(
                    run_dir,
                    SKILL_ROOT / "assets" / "dashboard-template.html",
                    output,
                )
                self.assertIsNotNone(payload_from(output.read_text(encoding="utf-8"))["visual_evidence"])

    def test_copy_only_run_rejects_a_saliency_file(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            run_dir = copy_fixture(Path(temp_dir), COPY_ONLY_FIXTURE)
            shutil.copy(FIXTURE / "saliency-index.json", run_dir / "saliency-index.json")
            saliency_path = run_dir / "saliency-index.json"
            saliency = json.loads(saliency_path.read_text(encoding="utf-8"))
            saliency["study_id"] = "dashboard-copy-001"
            saliency_path.write_text(json.dumps(saliency), encoding="utf-8")
            with self.assertRaisesRegex(DashboardInputError, "must not include"):
                render_dashboard(
                    run_dir,
                    SKILL_ROOT / "assets" / "dashboard-template.html",
                    Path(temp_dir) / "dashboard.html",
                )

    def test_post_reveal_roster_change_is_labeled_human_override(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            run_dir = copy_fixture(Path(temp_dir))
            finalist_path = run_dir / "finalist-results.json"
            finalists = json.loads(finalist_path.read_text(encoding="utf-8"))
            finalists["roster_decision"]["changed_after_saliency_reveal"] = True
            finalist_path.write_text(json.dumps(finalists), encoding="utf-8")
            output = Path(temp_dir) / "dashboard.html"
            render_dashboard(
                run_dir,
                SKILL_ROOT / "assets" / "dashboard-template.html",
                output,
            )
            html = output.read_text(encoding="utf-8")

        self.assertIn("saliency-informed human override", html)


class DashboardCrossFileIntegrityTests(unittest.TestCase):
    def test_disconnected_study_never_claims_the_graph_was_connected(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            run_dir = copy_fixture(Path(temp_dir), COPY_ONLY_FIXTURE)
            path = run_dir / "screening-model-results.json"
            results = read_json(path)
            results["model_diagnostics"]["connected"] = False
            write_json(path, results)
            payload = payload_from(render_fixture_dashboard(run_dir))

        design = next(
            item
            for item in payload["methodology"]["run_integrity"]
            if item["dimension"] == "Design adequacy"
        )
        self.assertNotIn("graph was connected", design["overview"].lower())
        self.assertIn("graph was disconnected", design["overview"].lower())

    def test_validator_derives_graph_statement_from_exported_diagnostics(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            output = Path(temp_dir) / "dashboard.html"
            render_to(COPY_ONLY_FIXTURE, output)
            rewrite_dashboard_payload(
                output,
                lambda payload: replace_json_export(
                    payload,
                    "screening-model-results.json",
                    lambda source: source["model_diagnostics"].update(connected=False),
                ),
            )
            result = validator_result(output)

        self.assertNotEqual(0, result.returncode)
        self.assertIn("comparison graph", result.stdout.lower())

    def test_screening_result_keys_must_match_the_roster(self):
        for field in ("utilities", "top_k_inclusion_frequencies", "classifications"):
            with self.subTest(field=field), tempfile.TemporaryDirectory() as temp_dir:
                run_dir = copy_fixture(Path(temp_dir))
                path = run_dir / "screening-model-results.json"
                results = read_json(path)
                results[field].pop("creative-d")
                write_json(path, results)
                with self.assertRaisesRegex(DashboardInputError, rf"{field} keys.*creative roster"):
                    render_to(run_dir, Path(temp_dir) / "dashboard.html")

    def test_screening_rank_must_be_a_roster_permutation(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            run_dir = copy_fixture(Path(temp_dir))
            path = run_dir / "screening-model-results.json"
            results = read_json(path)
            results["ranked_ids"] = ["creative-a", "creative-a", "creative-c", "creative-d"]
            write_json(path, results)
            with self.assertRaisesRegex(DashboardInputError, "ranked_ids.*permutation"):
                render_to(run_dir, Path(temp_dir) / "dashboard.html")

    def test_manifest_and_screening_validity_must_agree(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            run_dir = copy_fixture(Path(temp_dir))
            path = run_dir / "study-manifest.json"
            manifest = read_json(path)
            manifest["validity_status"] = "valid"
            write_json(path, manifest)
            with self.assertRaisesRegex(DashboardInputError, "validity_status.*must match"):
                render_to(run_dir, Path(temp_dir) / "dashboard.html")

    def test_retry_bearing_calls_and_accepted_replicates_have_separate_denominators(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            run_dir = copy_fixture(Path(temp_dir), COPY_ONLY_FIXTURE)
            path = run_dir / "study-manifest.json"
            manifest = read_json(path)
            manifest["usage"]["total_model_calls"] = 3
            write_json(path, manifest)
            output = Path(temp_dir) / "dashboard.html"
            render_to(run_dir, output)
            payload = payload_from(output.read_text(encoding="utf-8"))
            result = validator_result(output)

        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        denominators = payload["summary"]["denominators"]
        self.assertEqual(3, denominators["total_model_calls"])
        self.assertEqual(2, denominators["accepted_response_records"])
        self.assertEqual(2, denominators["accepted_unique_replicates"])
        self.assertEqual(
            {"finalist": 1, "screening": 1},
            payload["summary"]["accepted_response_records_by_stage"],
        )
        self.assertEqual(
            {"finalist": 1, "screening": 1},
            payload["summary"]["accepted_unique_replicates_by_stage"],
        )

    def test_grounded_profile_count_is_manifest_sourced_not_replicate_derived(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            run_dir = copy_fixture(Path(temp_dir), COPY_ONLY_FIXTURE)
            path = run_dir / "study-manifest.json"
            manifest = read_json(path)
            manifest["audience_lock"]["unique_grounded_context_profiles"] = 7
            write_json(path, manifest)
            output = Path(temp_dir) / "dashboard.html"
            render_to(run_dir, output)
            payload = payload_from(output.read_text(encoding="utf-8"))
            result = validator_result(output)

        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        self.assertEqual(
            7, payload["summary"]["denominators"]["grounded_context_profiles"]
        )
        self.assertEqual(
            2, payload["summary"]["denominators"]["accepted_unique_replicates"]
        )

    def test_named_context_strata_survive_into_dashboard_denominators(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            run_dir = copy_fixture(Path(temp_dir), COPY_ONLY_FIXTURE)
            path = run_dir / "panelist-responses.jsonl"
            responses = [
                json.loads(line)
                for line in path.read_text(encoding="utf-8").splitlines()
                if line
            ]
            context_ids = (
                "marketing-leader-evaluation",
                "finance-leader-discovery",
            )
            for response, context_id in zip(responses, context_ids, strict=True):
                response["context_stratum_id"] = context_id
            path.write_text(
                "".join(json.dumps(response) + "\n" for response in responses),
                encoding="utf-8",
            )
            output = Path(temp_dir) / "dashboard.html"
            render_to(run_dir, output)
            payload = payload_from(output.read_text(encoding="utf-8"))
            result = validator_result(output)

        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        self.assertEqual(
            2, payload["summary"]["denominators"]["accepted_context_strata"]
        )
        self.assertEqual(
            set(context_ids),
            {response["context_stratum_id"] for response in payload["responses"]},
        )

    def test_total_model_calls_cannot_be_less_than_accepted_records(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            run_dir = copy_fixture(Path(temp_dir), COPY_ONLY_FIXTURE)
            path = run_dir / "study-manifest.json"
            manifest = read_json(path)
            manifest["usage"]["total_model_calls"] = 1
            write_json(path, manifest)
            with self.assertRaisesRegex(
                DashboardInputError, "total_model_calls.*accepted response records"
            ):
                render_to(run_dir, Path(temp_dir) / "dashboard.html")

    def test_finalist_roster_is_unique_requested_size_and_in_roster(self):
        cases = {
            "duplicate": ["creative-a", "creative-a"],
            "wrong size": ["creative-a"],
            "unknown": ["creative-a", "creative-z"],
        }
        for label, finalist_ids in cases.items():
            with self.subTest(label=label), tempfile.TemporaryDirectory() as temp_dir:
                run_dir = copy_fixture(Path(temp_dir))
                path = run_dir / "finalist-results.json"
                finalists = read_json(path)
                finalists["approved_finalist_ids"] = finalist_ids
                write_json(path, finalists)
                with self.assertRaisesRegex(DashboardInputError, "finalist roster"):
                    render_to(run_dir, Path(temp_dir) / "dashboard.html")

    def test_each_finalist_response_exactly_matches_the_finalist_set(self):
        mutations = {
            "assignment": lambda row: row.update(
                assigned_variation_ids=["creative-a"], shown_order=["creative-a"]
            ),
            "reviews": lambda row: row.update(finalist_reviews=row["finalist_reviews"][:1]),
            "ranking": lambda row: row.update(final_preference_ranking=["creative-a"]),
        }
        for label, mutate in mutations.items():
            with self.subTest(label=label), tempfile.TemporaryDirectory() as temp_dir:
                run_dir = copy_fixture(Path(temp_dir))
                path = run_dir / "panelist-responses.jsonl"
                rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
                finalist = next(row for row in rows if row["record_type"] == "finalist_response")
                mutate(finalist)
                path.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")
                with self.assertRaisesRegex(DashboardInputError, "finalist response.*finalist set"):
                    render_to(run_dir, Path(temp_dir) / "dashboard.html")

    def test_finalist_counts_and_shares_are_finite_normalized_and_derived(self):
        cases = {
            "count total": lambda value: value["first_choice_counts"].update({"creative-a": 13}),
            "derived share": lambda value: value["conditional_first_choice_share"].update({"creative-a": 0.9}),
            "normalized share": lambda value: value["conditional_first_choice_share"].update(
                {"creative-a": float("nan")}
            ),
        }
        for label, mutate in cases.items():
            with self.subTest(label=label), tempfile.TemporaryDirectory() as temp_dir:
                run_dir = copy_fixture(Path(temp_dir))
                path = run_dir / "finalist-results.json"
                finalists = read_json(path)
                mutate(finalists)
                write_json(path, finalists)
                with self.assertRaisesRegex(DashboardInputError, "first-choice counts|conditional shares"):
                    render_to(run_dir, Path(temp_dir) / "dashboard.html")

    def test_feedback_sources_must_exist_and_match_stage_assignment_and_base(self):
        cases = {
            "unknown response": lambda theme: theme.update(response_ids=["missing-response"]),
            "wrong assignment": lambda theme: theme.update(
                creative_id="creative-c", response_ids=["response-final-01"]
            ),
            "base below sources": lambda theme: theme["exposed_base"].update(count=0),
        }
        for label, mutate in cases.items():
            with self.subTest(label=label), tempfile.TemporaryDirectory() as temp_dir:
                run_dir = copy_fixture(Path(temp_dir))
                path = run_dir / "feedback-synthesis.json"
                feedback = read_json(path)
                mutate(feedback["themes"][0])
                write_json(path, feedback)
                with self.assertRaisesRegex(DashboardInputError, "feedback-synthesis.*response|exposed_base"):
                    render_to(run_dir, Path(temp_dir) / "dashboard.html")

    def test_feedback_contract_requires_every_actionable_field(self):
        required_fields = (
            "stage",
            "creative_id",
            "segment_id",
            "lane",
            "feedback_type",
            "evidence_scope",
            "theme",
            "why_it_matters",
            "recommended_action",
            "source_type",
            "response_ids",
            "exposed_base",
            "limitations",
        )
        for field in required_fields:
            with self.subTest(field=field), tempfile.TemporaryDirectory() as temp_dir:
                run_dir = copy_fixture(Path(temp_dir))
                path = run_dir / "feedback-synthesis.json"
                feedback = read_json(path)
                feedback["themes"][0].pop(field)
                write_json(path, feedback)
                with self.assertRaisesRegex(DashboardInputError, "feedback-synthesis"):
                    render_to(run_dir, Path(temp_dir) / "dashboard.html")

    def test_feedback_type_and_limitations_are_strict(self):
        cases = {
            "bad type": lambda theme: theme.update(feedback_type="insight"),
            "bad evidence scope": lambda theme: theme.update(evidence_scope="consensus"),
            "empty limitations": lambda theme: theme.update(limitations=[]),
            "empty base label": lambda theme: theme["exposed_base"].update(label=""),
        }
        for label, mutate in cases.items():
            with self.subTest(label=label), tempfile.TemporaryDirectory() as temp_dir:
                run_dir = copy_fixture(Path(temp_dir))
                path = run_dir / "feedback-synthesis.json"
                feedback = read_json(path)
                mutate(feedback["themes"][0])
                write_json(path, feedback)
                with self.assertRaisesRegex(
                    DashboardInputError,
                    "feedback_type|evidence_scope|limitations|exposed_base.label",
                ):
                    render_to(run_dir, Path(temp_dir) / "dashboard.html")

    def test_one_response_cannot_claim_a_repeated_pattern(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            run_dir = copy_fixture(Path(temp_dir))
            path = run_dir / "feedback-synthesis.json"
            feedback = read_json(path)
            feedback["themes"][0]["evidence_scope"] = "cross_response_pattern"
            write_json(path, feedback)
            with self.assertRaisesRegex(DashboardInputError, "cannot claim.*pattern"):
                render_to(run_dir, Path(temp_dir) / "dashboard.html")

    def test_single_source_observation_must_be_visibly_limited(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            run_dir = copy_fixture(Path(temp_dir))
            path = run_dir / "feedback-synthesis.json"
            feedback = read_json(path)
            feedback["themes"][0]["limitations"] = ["Synthetic evidence only."]
            write_json(path, feedback)
            with self.assertRaisesRegex(DashboardInputError, "single-source"):
                render_to(run_dir, Path(temp_dir) / "dashboard.html")

    def test_feedback_coverage_is_required_for_every_creative_and_top_ad(self):
        cases = {
            "creative coverage": lambda themes: themes.__setitem__(
                slice(None), [item for item in themes if item["creative_id"] != "creative-d"]
            ),
            "top-ad next test": lambda themes: themes.__setitem__(
                slice(None),
                [
                    item
                    for item in themes
                    if not (
                        item["creative_id"] == "creative-a"
                        and item["feedback_type"] == "next_test"
                    )
                ],
            ),
        }
        for label, mutate in cases.items():
            with self.subTest(label=label), tempfile.TemporaryDirectory() as temp_dir:
                run_dir = copy_fixture(Path(temp_dir))
                path = run_dir / "feedback-synthesis.json"
                feedback = read_json(path)
                mutate(feedback["themes"])
                write_json(path, feedback)
                with self.assertRaisesRegex(DashboardInputError, "coverage gap"):
                    render_to(run_dir, Path(temp_dir) / "dashboard.html")

    def test_positive_only_top_ad_does_not_require_invented_friction(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            run_dir = copy_fixture(Path(temp_dir), COPY_ONLY_FIXTURE)
            path = run_dir / "feedback-synthesis.json"
            feedback = read_json(path)
            feedback["themes"] = [
                item for item in feedback["themes"] if item["feedback_type"] != "friction"
            ]
            write_json(path, feedback)
            render_to(run_dir, Path(temp_dir) / "dashboard.html")

    def test_feedback_claims_and_change_actions_cannot_overstate_evidence(self):
        cases = {
            "survey percentage": lambda theme: theme.update(
                theme="72% of customers preferred this ad."
            ),
            "population preference": lambda theme: theme.update(
                why_it_matters="Customers prefer this approach."
            ),
            "guaranteed performance": lambda theme: theme.update(
                recommended_action="Test this version because it will improve conversion."
            ),
            "untested change": lambda theme: theme.update(
                recommended_action="Replace the call to action with a shorter phrase."
            ),
            "test cannot sanitize proven fix": lambda theme: theme.update(
                recommended_action="Test the proven fix: replace the headline."
            ),
            "test cannot sanitize proven improvement": lambda theme: theme.update(
                recommended_action="Test this proven improvement."
            ),
        }
        for label, mutate in cases.items():
            with self.subTest(label=label), tempfile.TemporaryDirectory() as temp_dir:
                run_dir = copy_fixture(Path(temp_dir))
                path = run_dir / "feedback-synthesis.json"
                feedback = read_json(path)
                mutate(feedback["themes"][1])
                write_json(path, feedback)
                with self.assertRaisesRegex(
                    DashboardInputError,
                    "survey-style|customer or population|performance outcomes|test or hypothesis",
                ):
                    render_to(run_dir, Path(temp_dir) / "dashboard.html")

    def test_explicitly_negated_feedback_claim_is_allowed(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            run_dir = copy_fixture(Path(temp_dir))
            path = run_dir / "feedback-synthesis.json"
            feedback = read_json(path)
            feedback["themes"][0]["limitations"] = [
                "Single-source observation. This is not proof that customers prefer this ad."
            ]
            write_json(path, feedback)
            output = Path(temp_dir) / "dashboard.html"
            render_to(run_dir, output)
            result = validator_result(output)
            self.assertEqual(0, result.returncode, result.stdout + result.stderr)

    def test_validator_rechecks_source_exports_and_rendered_metrics(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            output = Path(temp_dir) / "dashboard.html"
            render_to(FIXTURE, output)
            rewrite_dashboard_payload(
                output,
                lambda payload: payload["finalists"]["conditional_first_choice_share"].update(
                    {"creative-a": 0.9}
                ),
            )
            result = validator_result(output)
            self.assertNotEqual(0, result.returncode)
            self.assertIn("source export", result.stdout)

        with tempfile.TemporaryDirectory() as temp_dir:
            output = Path(temp_dir) / "dashboard.html"
            render_to(FIXTURE, output)
            rewrite_dashboard_payload(
                output,
                lambda payload: replace_json_export(
                    payload,
                    "screening-model-results.json",
                    lambda source: source.update(validity_status="valid"),
                ),
            )
            result = validator_result(output)
            self.assertNotEqual(0, result.returncode)
            self.assertIn("cross-file", result.stdout)

    def test_standalone_validator_rejects_missing_rich_feedback_fields(self):
        fields = (
            "feedback_type",
            "evidence_scope",
            "why_it_matters",
            "recommended_action",
            "limitations",
        )
        for surface in ("source", "rendered"):
            for field in fields:
                with self.subTest(surface=surface, field=field), tempfile.TemporaryDirectory() as temp_dir:
                    output = Path(temp_dir) / "dashboard.html"
                    render_to(FIXTURE, output)
                    if surface == "source":
                        rewrite_dashboard_payload(
                            output,
                            lambda payload, field=field: replace_json_export(
                                payload,
                                "feedback-synthesis.json",
                                lambda source: source["themes"][0].pop(field),
                            ),
                        )
                    else:
                        rewrite_dashboard_payload(
                            output,
                            lambda payload, field=field: payload["feedback"][0].pop(field),
                        )
                    result = validator_result(output)
                    self.assertNotEqual(0, result.returncode)
                    self.assertIn("feedback", result.stdout.lower())

    def test_standalone_validator_rejects_feedback_claims_and_untested_changes(self):
        mutations = (
            ("theme", "68% of customers preferred this ad."),
            ("why_it_matters", "This proves the ad will increase sales."),
            (
                "recommended_action",
                "Replace the call to action with a shorter phrase.",
            ),
            ("recommended_action", "Test the proven fix: replace the headline."),
            ("recommended_action", "Test this proven improvement."),
        )
        for surface in ("source", "rendered"):
            for field, value in mutations:
                with self.subTest(surface=surface, field=field), tempfile.TemporaryDirectory() as temp_dir:
                    output = Path(temp_dir) / "dashboard.html"
                    render_to(FIXTURE, output)
                    if surface == "source":
                        rewrite_dashboard_payload(
                            output,
                            lambda payload, field=field, value=value: replace_json_export(
                                payload,
                                "feedback-synthesis.json",
                                lambda source: source["themes"][1].update({field: value}),
                            ),
                        )
                    else:
                        rewrite_dashboard_payload(
                            output,
                            lambda payload, field=field, value=value: payload["feedback"][1].update(
                                {field: value}
                            ),
                        )
                    result = validator_result(output)
                    self.assertNotEqual(0, result.returncode)
                    self.assertRegex(
                        result.stdout.lower(),
                        "survey-style|performance|test or hypothesis|does not exactly match",
                    )

    def test_standalone_validator_allows_synchronized_negated_caveat(self):
        caveat = (
            "Single-source observation. This is not proof that customers prefer this ad."
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            output = Path(temp_dir) / "dashboard.html"
            render_to(FIXTURE, output)

            def mutate(payload: dict) -> None:
                payload["feedback"][0]["limitations"] = [caveat]
                replace_json_export(
                    payload,
                    "feedback-synthesis.json",
                    lambda source: source["themes"][0].update(limitations=[caveat]),
                )

            rewrite_dashboard_payload(output, mutate)
            result = validator_result(output)
            self.assertEqual(0, result.returncode, result.stdout + result.stderr)

    def test_validator_binds_exported_media_tuples_to_rendered_media(self):
        fake_hash = "sha256:" + "f" * 64

        def mutate_exports(payload: dict) -> None:
            replace_json_export(
                payload,
                "creative-roster.json",
                lambda source: source["creatives"][0]["media"][0].update(
                    content_hash=fake_hash
                ),
            )
            replace_json_export(
                payload,
                "saliency-index.json",
                lambda source: source["entries"][0].update(content_hash=fake_hash),
            )

        with tempfile.TemporaryDirectory() as temp_dir:
            output = Path(temp_dir) / "dashboard.html"
            render_to(FIXTURE, output)
            rewrite_dashboard_payload(output, mutate_exports)
            result = validator_result(output)

        self.assertNotEqual(0, result.returncode)
        self.assertIn("media representation", result.stdout.lower())


class DashboardRepresentationAndProvenanceTests(unittest.TestCase):
    def test_visual_entries_expose_the_exact_representation_id_and_hash(self):
        payload = payload_from(render_fixture_dashboard())
        medium = payload["creatives"][0]["media"][0]
        visual = payload["visual_evidence"]["entries"][0]
        self.assertEqual("creative-a-static-01", medium["representation_id"])
        self.assertEqual(medium["representation_id"], visual["representation_id"])
        self.assertEqual(medium["content_hash"], visual["content_hash"])

    def test_visual_entries_expose_the_verified_overlay_content_hash(self):
        payload = payload_from(render_fixture_dashboard())
        visual = payload["visual_evidence"]["entries"][0]
        self.assertEqual(
            "sha256:2a8b2adea6041673ebfc6e859c1f6630d35d0f1d78ec2bf501e18667f3f8acdf",
            visual["overlay_content_hash"],
        )

    def test_overlay_content_hash_must_match_the_actual_overlay_file(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            run_dir = copy_fixture(Path(temp_dir))
            path = run_dir / "saliency-index.json"
            saliency = read_json(path)
            saliency["entries"][0]["overlay_content_hash"] = "sha256:" + "0" * 64
            write_json(path, saliency)

            with self.assertRaisesRegex(DashboardInputError, "overlay_content_hash"):
                render_to(run_dir, Path(temp_dir) / "dashboard.html")

    def test_validator_binds_every_rendered_saliency_field_to_source_export(self):
        def provider(payload: dict) -> None:
            payload["visual_evidence"]["provider"] = "Other provider"
            payload["visual_evidence"]["entries"][0]["provider"] = "Other provider"

        cases = {
            "provider": provider,
            "method": lambda payload: payload["visual_evidence"].update(
                method="other saliency method"
            ),
            "revealed_at": lambda payload: payload["visual_evidence"].update(
                revealed_at="2026-07-21T15:05:30+00:00"
            ),
            "predeclared_target": lambda payload: payload["visual_evidence"]["entries"][
                0
            ].update(predeclared_target="A different predeclared target"),
            "target_declared_at": lambda payload: payload["visual_evidence"]["entries"][
                0
            ].update(target_declared_at="2026-07-21T15:03:00+00:00"),
            "categorical_alignment": lambda payload: payload["visual_evidence"]["entries"][
                0
            ].update(categorical_alignment="aligned"),
            "limitations": lambda payload: payload["visual_evidence"]["entries"][0].update(
                limitations=["A different but nonempty limitation."]
            ),
            "original_data_url": lambda payload: payload["visual_evidence"]["entries"][
                0
            ].update(
                original_data_url=payload["visual_evidence"]["entries"][0][
                    "overlay_data_url"
                ]
            ),
            "overlay_data_url": lambda payload: payload["visual_evidence"]["entries"][
                0
            ].update(
                overlay_data_url=payload["visual_evidence"]["entries"][0][
                    "original_data_url"
                ]
            ),
        }
        for field, mutate in cases.items():
            with self.subTest(field=field), tempfile.TemporaryDirectory() as temp_dir:
                output = Path(temp_dir) / "dashboard.html"
                render_to(FIXTURE, output)
                rewrite_dashboard_payload(output, mutate)
                result = validator_result(output)
                self.assertNotEqual(0, result.returncode, field)
                self.assertIn("source export", result.stdout.lower(), field)

    def test_validator_binds_overlay_hash_and_bytes_to_source_export(self):
        cases = {
            "rendered hash": lambda payload: payload["visual_evidence"]["entries"][0].update(
                overlay_content_hash="sha256:" + "f" * 64
            ),
            "source hash": lambda payload: replace_json_export(
                payload,
                "saliency-index.json",
                lambda source: source["entries"][0].update(
                    overlay_content_hash="sha256:" + "f" * 64
                ),
            ),
        }
        for field, mutate in cases.items():
            with self.subTest(field=field), tempfile.TemporaryDirectory() as temp_dir:
                output = Path(temp_dir) / "dashboard.html"
                render_to(FIXTURE, output)
                rewrite_dashboard_payload(output, mutate)
                result = validator_result(output)
                self.assertNotEqual(0, result.returncode, field)
                self.assertIn("overlay", result.stdout.lower(), field)

    def test_validator_binds_every_rendered_creative_field_to_source_export(self):
        def mutate_media_bytes(payload: dict) -> None:
            medium = payload["creatives"][0]["media"][0]
            visual = payload["visual_evidence"]["entries"][0]
            raw, _ = _decode_test_data_url(visual["overlay_data_url"])
            replacement_hash = f"sha256:{hashlib.sha256(raw).hexdigest()}"
            medium.update(
                data_url=visual["overlay_data_url"],
                content_hash=replacement_hash,
                byte_count=len(raw),
            )
            visual.update(
                original_data_url=visual["overlay_data_url"],
                content_hash=replacement_hash,
            )

        cases = {
            "display_name": lambda payload: payload["creatives"][0].update(
                display_name="Changed display name"
            ),
            "headline": lambda payload: payload["creatives"][0].update(
                headline="Changed headline"
            ),
            "body": lambda payload: payload["creatives"][0].update(body="Changed body"),
            "cta": lambda payload: payload["creatives"][0].update(cta="Changed CTA"),
            "visual_description": lambda payload: payload["creatives"][0].update(
                visual_description="Changed visual description"
            ),
            "input_fidelity": lambda payload: payload["creatives"][0].update(
                input_fidelity="changed fidelity"
            ),
            "format": lambda payload: payload["creatives"][0].update(format="changed format"),
            "media kind": lambda payload: payload["creatives"][0]["media"][0].update(
                kind="video"
            ),
            "media label": lambda payload: payload["creatives"][0]["media"][0].update(
                label="Changed label"
            ),
            "media alt": lambda payload: payload["creatives"][0]["media"][0].update(
                alt="Changed alt text"
            ),
            "media mime": lambda payload: payload["creatives"][0]["media"][0].update(
                mime_type="image/png",
                data_url=payload["creatives"][0]["media"][0]["data_url"].replace(
                    "data:image/svg+xml;base64,", "data:image/png;base64,", 1
                ),
            ),
            "media byte_count": lambda payload: payload["creatives"][0]["media"][0].update(
                byte_count=payload["creatives"][0]["media"][0]["byte_count"] + 1
            ),
            "media availability": lambda payload: payload["creatives"][0]["media"][0].update(
                availability="remote"
            ),
            "media bytes": mutate_media_bytes,
        }
        for field, mutate in cases.items():
            with self.subTest(field=field), tempfile.TemporaryDirectory() as temp_dir:
                output = Path(temp_dir) / "dashboard.html"
                render_to(FIXTURE, output)
                rewrite_dashboard_payload(output, mutate)
                result = validator_result(output)
                self.assertNotEqual(0, result.returncode, field)
                self.assertIn("source export", result.stdout.lower(), field)

    def test_each_media_representation_requires_its_own_saliency_entry(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            run_dir = copy_fixture(Path(temp_dir))
            path = run_dir / "creative-roster.json"
            roster = read_json(path)
            second = deepcopy(roster["creatives"][0]["media"][0])
            second["representation_id"] = "creative-a-static-02"
            second["label"] = "Second carousel card"
            roster["creatives"][0]["media"].append(second)
            write_json(path, roster)
            with self.assertRaisesRegex(DashboardInputError, "missing.*media representations.*creative-a-static-02"):
                render_to(run_dir, Path(temp_dir) / "dashboard.html")

    def test_media_and_saliency_hashes_must_match_the_actual_original(self):
        for filename, path_parts in (
            ("creative-roster.json", ("creatives", 0, "media", 0)),
            ("saliency-index.json", ("entries", 0)),
        ):
            with self.subTest(filename=filename), tempfile.TemporaryDirectory() as temp_dir:
                run_dir = copy_fixture(Path(temp_dir))
                path = run_dir / filename
                payload = read_json(path)
                item = payload
                for part in path_parts:
                    item = item[part]
                item["content_hash"] = "sha256:" + "0" * 64
                write_json(path, payload)
                with self.assertRaisesRegex(DashboardInputError, "content_hash"):
                    render_to(run_dir, Path(temp_dir) / "dashboard.html")

    def test_tested_originals_and_heatmap_overlays_require_image_mime(self):
        for target in ("original", "overlay"):
            with self.subTest(target=target), tempfile.TemporaryDirectory() as temp_dir:
                run_dir = copy_fixture(Path(temp_dir))
                bad_bytes = b"plain text is not renderable image evidence"
                bad_path = run_dir / "media" / f"not-an-image-{target}.txt"
                bad_path.write_bytes(bad_bytes)
                bad_hash = f"sha256:{hashlib.sha256(bad_bytes).hexdigest()}"
                saliency_path = run_dir / "saliency-index.json"
                saliency = read_json(saliency_path)
                if target == "original":
                    roster_path = run_dir / "creative-roster.json"
                    roster = read_json(roster_path)
                    roster["creatives"][0]["media"][0].update(
                        path=f"media/{bad_path.name}", content_hash=bad_hash
                    )
                    saliency["entries"][0].update(
                        original_path=f"media/{bad_path.name}", content_hash=bad_hash
                    )
                    write_json(roster_path, roster)
                else:
                    saliency["entries"][0]["overlay_path"] = f"media/{bad_path.name}"
                write_json(saliency_path, saliency)

                with self.assertRaisesRegex(
                    DashboardInputError, "renderable image MIME"
                ):
                    render_to(run_dir, Path(temp_dir) / "dashboard.html")

    def test_validator_rejects_image_bytes_labeled_as_text_plain(self):
        def mutate(payload: dict) -> None:
            medium = payload["creatives"][0]["media"][0]
            visual = payload["visual_evidence"]["entries"][0]
            text_url = medium["data_url"].replace(
                "data:image/svg+xml;base64,", "data:text/plain;base64,", 1
            )
            medium["data_url"] = text_url
            medium["mime_type"] = "text/plain"
            visual["original_data_url"] = text_url
            visual["original_mime_type"] = "text/plain"

        with tempfile.TemporaryDirectory() as temp_dir:
            output = Path(temp_dir) / "dashboard.html"
            render_to(FIXTURE, output)
            rewrite_dashboard_payload(output, mutate)
            result = validator_result(output)

        self.assertNotEqual(0, result.returncode)
        self.assertIn("image mime", result.stdout.lower())

    def test_noncanonical_video_format_is_rejected(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            run_dir = copy_fixture(Path(temp_dir))
            path = run_dir / "study-manifest.json"
            manifest = read_json(path)
            manifest["creative_format"] = "video_frame_representation"
            write_json(path, manifest)
            with self.assertRaisesRegex(DashboardInputError, "canonical creative_format"):
                render_to(run_dir, Path(temp_dir) / "dashboard.html")

    def test_saliency_provenance_never_uses_truthy_placeholders(self):
        cases = {
            "provider": lambda value: value.update(provider=""),
            "method": lambda value: value.update(method=""),
            "entry provider": lambda value: value["entries"][0].update(provider=""),
            "predeclared_target": lambda value: value["entries"][0].update(predeclared_target=""),
            "categorical_alignment": lambda value: value["entries"][0].update(categorical_alignment=""),
            "limitations": lambda value: value["entries"][0].update(limitations=[]),
        }
        for field, mutate in cases.items():
            with self.subTest(field=field), tempfile.TemporaryDirectory() as temp_dir:
                run_dir = copy_fixture(Path(temp_dir))
                path = run_dir / "saliency-index.json"
                saliency = read_json(path)
                mutate(saliency)
                write_json(path, saliency)
                with self.assertRaisesRegex(DashboardInputError, re.escape(field.split()[-1])):
                    render_to(run_dir, Path(temp_dir) / "dashboard.html")

    def test_target_must_be_timestamped_strictly_before_saliency_reveal(self):
        for value in (
            None,
            "2026-07-21T15:05:00Z",
            "2026-07-21T15:06:00Z",
        ):
            with self.subTest(value=value), tempfile.TemporaryDirectory() as temp_dir:
                run_dir = copy_fixture(Path(temp_dir))
                path = run_dir / "saliency-index.json"
                saliency = read_json(path)
                if value is None:
                    saliency["entries"][0].pop("target_declared_at")
                else:
                    saliency["entries"][0]["target_declared_at"] = value
                write_json(path, saliency)
                with self.assertRaisesRegex(DashboardInputError, "target_declared_at.*before.*revealed_at"):
                    render_to(run_dir, Path(temp_dir) / "dashboard.html")

    def test_roster_approval_must_be_strictly_before_saliency_reveal(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            run_dir = copy_fixture(Path(temp_dir))
            path = run_dir / "finalist-results.json"
            finalists = read_json(path)
            finalists["roster_decision"]["approved_at"] = "2026-07-21T15:05:00Z"
            write_json(path, finalists)
            with self.assertRaisesRegex(
                DashboardInputError, "roster approval.*before reveal"
            ):
                render_to(run_dir, Path(temp_dir) / "dashboard.html")

    def test_approval_and_reveal_timestamps_require_timezones(self):
        cases = (
            ("finalist-results.json", "roster_decision", "approved_at"),
            ("saliency-index.json", None, "revealed_at"),
        )
        for filename, nested, field in cases:
            with self.subTest(field=field), tempfile.TemporaryDirectory() as temp_dir:
                run_dir = copy_fixture(Path(temp_dir))
                path = run_dir / filename
                value = read_json(path)
                target = value[nested] if nested else value
                target[field] = "2026-07-21T15:00:00"
                write_json(path, value)
                with self.assertRaisesRegex(DashboardInputError, rf"{field}.*timezone"):
                    render_to(run_dir, Path(temp_dir) / "dashboard.html")


class DashboardMethodAndApprovalTests(unittest.TestCase):
    def test_all_five_integrity_dimensions_and_run_limits_are_in_methodology(self):
        payload = payload_from(render_fixture_dashboard())
        dimensions = [item["dimension"] for item in payload["methodology"]["run_integrity"]]
        self.assertEqual(
            ["Research basis", "Input fidelity", "Review integrity", "Design adequacy", "Result stability"],
            dimensions,
        )
        self.assertEqual(payload["summary"]["run_integrity"], payload["methodology"]["run_integrity"])
        self.assertEqual(
            payload["screening"]["interpretation_limits"],
            payload["methodology"]["interpretation_limits"],
        )

    def test_complete_exposure_methodology_does_not_claim_maxdiff_or_four_ad_subsets(self):
        payload = payload_from(render_fixture_dashboard(COPY_ONLY_FIXTURE))
        methodology_text = json.dumps(payload["methodology"])
        self.assertEqual("complete_exposure", payload["methodology"]["method_id"])
        self.assertNotIn("MaxDiff", methodology_text)
        self.assertNotIn("four-ad", methodology_text)
        self.assertIn("every ad", methodology_text.lower())

    def test_unknown_method_is_rejected_explicitly(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            run_dir = copy_fixture(Path(temp_dir), COPY_ONLY_FIXTURE)
            path = run_dir / "study-manifest.json"
            manifest = read_json(path)
            manifest["method"] = "mystery_method"
            write_json(path, manifest)
            with self.assertRaisesRegex(DashboardInputError, "unsupported study method"):
                render_to(run_dir, Path(temp_dir) / "dashboard.html")

    def test_awaiting_approval_uses_pending_language_and_hides_finalist_metrics(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            run_dir = copy_fixture(Path(temp_dir), COPY_ONLY_FIXTURE)
            path = run_dir / "finalist-results.json"
            finalists = read_json(path)
            finalists["roster_decision"] = {"status": "awaiting_approval", "override": False}
            write_json(path, finalists)
            payload = payload_from(render_fixture_dashboard(run_dir))

        summary = payload["summary"]
        self.assertFalse(summary["roster_decision"]["is_approved"])
        self.assertEqual([], summary["ads_moving_forward"])
        self.assertEqual(2, len(summary["ads_pending_approval"]))
        self.assertIn("pending approval", summary["overview_intro"].lower())
        self.assertNotIn("moving forward", summary["overview_intro"].lower())
        self.assertNotIn("made the cut", summary["overview_intro"].lower())
        self.assertFalse(payload["finalists"]["metrics_available"])
        self.assertEqual({}, payload["finalists"]["conditional_first_choice_share"])
        pending_text = json.dumps({"summary": summary, "finalists": payload["finalists"]}).lower()
        self.assertNotIn("moving forward", pending_text)
        self.assertNotIn("made the final shortlist decision", pending_text)
        self.assertIn("pending", pending_text)

    def test_awaiting_approval_allows_absent_finalist_metrics(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            run_dir = copy_fixture(Path(temp_dir), COPY_ONLY_FIXTURE)
            path = run_dir / "finalist-results.json"
            finalists = read_json(path)
            finalists["roster_decision"] = {
                "status": "awaiting_approval",
                "override": False,
            }
            for key in (
                "accepted_response_records",
                "accepted_unique_replicates",
                "unique_job_slots_consumed",
                "total_model_calls",
                "first_choice_counts",
                "conditional_first_choice_share",
                "rubric_summary",
                "model_conditional_agreement",
                "segment_contrasts",
                "testing_map",
            ):
                finalists.pop(key, None)
            write_json(path, finalists)
            output = Path(temp_dir) / "dashboard.html"
            render_to(run_dir, output)
            payload = payload_from(output.read_text(encoding="utf-8"))
            result = validator_result(output)

        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        self.assertFalse(payload["finalists"]["metrics_available"])
        self.assertIsNone(payload["finalists"]["accepted_response_records"])
        self.assertIsNone(payload["finalists"]["total_model_calls"])
        self.assertEqual({}, payload["finalists"]["first_choice_counts"])
        self.assertEqual({}, payload["finalists"]["conditional_first_choice_share"])

    def test_roster_override_status_and_flag_must_agree_both_ways(self):
        cases = (
            ("approved_with_override", False),
            ("approved", True),
        )
        for status, override in cases:
            with self.subTest(status=status, override=override), tempfile.TemporaryDirectory() as temp_dir:
                run_dir = copy_fixture(Path(temp_dir), COPY_ONLY_FIXTURE)
                path = run_dir / "finalist-results.json"
                finalists = read_json(path)
                finalists["roster_decision"].update(
                    status=status,
                    override=override,
                )
                write_json(path, finalists)
                with self.assertRaisesRegex(
                    DashboardInputError,
                    "approved_with_override.*override",
                ):
                    render_to(run_dir, Path(temp_dir) / "dashboard.html")

    def test_overview_heatmap_cta_moves_focus_to_activated_tab(self):
        template = (SKILL_ROOT / "assets" / "dashboard-template.html").read_text(encoding="utf-8")
        self.assertIn("heatmapTab.focus()", template)


class DashboardInputAndExportTests(unittest.TestCase):
    def test_missing_required_input_has_actionable_error(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            run_dir = Path(temp_dir)
            with self.assertRaisesRegex(
                DashboardInputError, "missing required dashboard input: study-manifest.json"
            ):
                render_dashboard(
                    run_dir,
                    SKILL_ROOT / "assets" / "dashboard-template.html",
                    run_dir / "dashboard.html",
                )

    def test_invalid_json_and_jsonl_have_file_context(self):
        for filename, content in (
            ("creative-roster.json", "{"),
            ("panelist-responses.jsonl", '{"response_id":'),
        ):
            with self.subTest(filename=filename), tempfile.TemporaryDirectory() as temp_dir:
                run_dir = copy_fixture(Path(temp_dir))
                (run_dir / filename).write_text(content, encoding="utf-8")
                with self.assertRaisesRegex(DashboardInputError, re.escape(filename)):
                    render_dashboard(
                        run_dir,
                        SKILL_ROOT / "assets" / "dashboard-template.html",
                        Path(temp_dir) / "dashboard.html",
                    )

    def test_study_id_mismatch_is_rejected(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            run_dir = copy_fixture(Path(temp_dir))
            results_path = run_dir / "screening-model-results.json"
            results = json.loads(results_path.read_text(encoding="utf-8"))
            results["study_id"] = "wrong-study"
            results_path.write_text(json.dumps(results), encoding="utf-8")
            with self.assertRaisesRegex(DashboardInputError, "study_id"):
                render_dashboard(
                    run_dir,
                    SKILL_ROOT / "assets" / "dashboard-template.html",
                    Path(temp_dir) / "dashboard.html",
                )

    def test_usable_screening_count_mismatch_is_rejected(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            run_dir = copy_fixture(Path(temp_dir))
            results_path = run_dir / "screening-model-results.json"
            results = json.loads(results_path.read_text(encoding="utf-8"))
            results["model_diagnostics"]["usable_observation_count"] = 99
            results_path.write_text(json.dumps(results), encoding="utf-8")
            with self.assertRaisesRegex(DashboardInputError, "usable_observation_count"):
                render_dashboard(
                    run_dir,
                    SKILL_ROOT / "assets" / "dashboard-template.html",
                    Path(temp_dir) / "dashboard.html",
                )

    def test_nonvalid_screening_requires_explicit_finalist_override(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            run_dir = copy_fixture(Path(temp_dir))
            finalist_path = run_dir / "finalist-results.json"
            finalists = json.loads(finalist_path.read_text(encoding="utf-8"))
            finalists["roster_decision"]["status"] = "approved"
            finalists["roster_decision"]["override"] = False
            finalist_path.write_text(json.dumps(finalists), encoding="utf-8")
            with self.assertRaisesRegex(DashboardInputError, "explicit human override"):
                render_dashboard(
                    run_dir,
                    SKILL_ROOT / "assets" / "dashboard-template.html",
                    Path(temp_dir) / "dashboard.html",
                )

    def test_exports_are_downloadable_self_contained_data_urls(self):
        payload = payload_from(render_fixture_dashboard())
        names = {item["filename"] for item in payload["exports"]}
        self.assertEqual(
            {
                "ai-audience-responses.csv",
                "study-manifest.json",
                "creative-roster.json",
                "panelist-responses.jsonl",
                "screening-model-results.json",
                "boundary-results.json",
                "finalist-results.json",
                "feedback-synthesis.json",
                "saliency-index.json",
            },
            names,
        )
        for item in payload["exports"]:
            self.assertTrue(item["data_url"].startswith("data:"))

    def test_response_csv_is_excel_friendly_deterministic_and_formula_safe(self):
        first = payload_from(render_fixture_dashboard())
        second = payload_from(render_fixture_dashboard())
        first_export = next(item for item in first["exports"] if item["filename"] == "ai-audience-responses.csv")
        second_export = next(item for item in second["exports"] if item["filename"] == "ai-audience-responses.csv")
        self.assertEqual(first_export["data_url"], second_export["data_url"])
        raw, mime = _decode_test_data_url(first_export["data_url"])
        self.assertEqual("text/csv", mime)
        rows = list(csv.DictReader(io.StringIO(raw.decode("utf-8"))))
        expected_rows = sum(len(item["assigned_creatives"]) for item in first["responses"])
        self.assertEqual(expected_rows, len(rows))
        self.assertEqual(
            ["test_round", "profile", "audience", "situation", "mindset", "ad", "immediate_reaction", "noticed_first", "positive_signal", "negative_signal", "overall_strongest_ad", "overall_weakest_ad", "overall_reason", "finalist_feedback", "rubric_scores", "response_id"],
            list(rows[0]),
        )
        self.assertTrue(any(row["immediate_reaction"] == "The delayed handoff is concrete and recognizable." for row in rows))
        finalist_row = next(row for row in rows if row["finalist_feedback"])
        self.assertEqual("Keep the workflow review concrete.", finalist_row["finalist_feedback"])
        self.assertIn("attention_potential: 4", finalist_row["rubric_scores"])
        for unsafe in ("=SUM(A1:A2)", "+cmd", "-1+2", "@malicious"):
            self.assertEqual("'" + unsafe, _csv_safe(unsafe))

    def test_attention_view_has_no_opacity_slider(self):
        html = render_fixture_dashboard(include_saliency=True)
        self.assertNotIn("Overlay opacity", html)
        self.assertNotIn('type="range"', html)
        self.assertNotIn("former control", html)
        self.assertNotIn("no opacity control is needed", html)
        self.assertIn("Compare the original ad with the predicted-attention view.", html)
        self.assertIn('make("div", "heatmap-picker")', html)
        self.assertIn('make("a", "full-size-link", "Download full-size original")', html)
        self.assertIn('make("a", "full-size-link", "Download full-size heatmap")', html)

    def test_cli_and_validator_accept_rendered_fixture(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            output = Path(temp_dir) / "dashboard.html"
            render = subprocess.run(
                [
                    sys.executable,
                    str(SKILL_ROOT / "scripts" / "render-dashboard.py"),
                    "--run-dir",
                    str(FIXTURE),
                    "--output",
                    str(output),
                ],
                cwd=ROOT,
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(0, render.returncode, render.stderr)
            validate = subprocess.run(
                [
                    sys.executable,
                    str(SKILL_ROOT / "scripts" / "validate-dashboard.py"),
                    str(output),
                ],
                cwd=ROOT,
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(0, validate.returncode, validate.stdout + validate.stderr)

    def test_validator_accepts_source_template_with_payload_labels_unrendered(self):
        result = subprocess.run(
            [
                sys.executable,
                str(SKILL_ROOT / "scripts" / "validate-dashboard.py"),
                str(SKILL_ROOT / "assets" / "dashboard-template.html"),
                "--allow-placeholders",
            ],
            cwd=ROOT,
            check=False,
            capture_output=True,
            text=True,
        )

        self.assertEqual(0, result.returncode, result.stdout + result.stderr)

    def test_validator_rejects_forbidden_label_in_embedded_data(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            run_dir = copy_fixture(Path(temp_dir))
            roster_path = run_dir / "creative-roster.json"
            roster = json.loads(roster_path.read_text(encoding="utf-8"))
            roster["creatives"][0]["display_name"] = "Confidence Rating"
            roster_path.write_text(json.dumps(roster), encoding="utf-8")
            output = Path(temp_dir) / "dashboard.html"
            render_dashboard(
                run_dir,
                SKILL_ROOT / "assets" / "dashboard-template.html",
                output,
            )
            result = subprocess.run(
                [sys.executable, str(SKILL_ROOT / "scripts" / "validate-dashboard.py"), str(output)],
                cwd=ROOT,
                check=False,
                capture_output=True,
                text=True,
            )

        self.assertNotEqual(0, result.returncode)
        self.assertIn("Forbidden dashboard terminology", result.stdout)

    def test_validator_rejects_unqualified_screening_percentage_claim(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            run_dir = copy_fixture(Path(temp_dir))
            feedback_path = run_dir / "feedback-synthesis.json"
            feedback = json.loads(feedback_path.read_text(encoding="utf-8"))
            feedback["themes"][0]["theme"] = "72% liked it after one exposure."
            feedback_path.write_text(json.dumps(feedback), encoding="utf-8")
            output = Path(temp_dir) / "dashboard.html"
            render_dashboard(
                run_dir,
                SKILL_ROOT / "assets" / "dashboard-template.html",
                output,
            )
            result = subprocess.run(
                [sys.executable, str(SKILL_ROOT / "scripts" / "validate-dashboard.py"), str(output)],
                cwd=ROOT,
                check=False,
                capture_output=True,
                text=True,
            )

        self.assertNotEqual(0, result.returncode)
        self.assertIn("unqualified screening percentage claim", result.stdout)

class DashboardTier4ValidationTests(unittest.TestCase):
    def test_canonical_shell_covers_all_optional_surface_combinations(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "dashboard_validator_test_module",
            SKILL_ROOT / "scripts" / "validate-dashboard.py",
        )
        assert spec is not None and spec.loader is not None
        validator = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(validator)
        from audience_lab.dashboard import (
            TIER4_VALIDATION_SCRIPT_PLACEHOLDER,
            TIER4_VALIDATION_SECTION_PLACEHOLDER,
            V3_ALLOCATION_SCRIPT_PLACEHOLDER,
            V3_ALLOCATION_SECTION_PLACEHOLDER,
        )

        for has_allocation in (False, True):
            for has_tier4 in (False, True):
                shell = validator._canonical_dashboard_shell(has_allocation, has_tier4)
                self.assertNotIn(V3_ALLOCATION_SECTION_PLACEHOLDER, shell)
                self.assertNotIn(V3_ALLOCATION_SCRIPT_PLACEHOLDER, shell)
                self.assertNotIn(TIER4_VALIDATION_SECTION_PLACEHOLDER, shell)
                self.assertNotIn(TIER4_VALIDATION_SCRIPT_PLACEHOLDER, shell)

    def test_authenticated_active_claim_renders_one_optional_section(self):
        from copy import deepcopy
        from unittest.mock import patch
        from conformance.test_tier4_validation_package import Tier4ValidationPackageTests
        from conformance.test_tier4_validation_package import (
            build_validation_package, validate_validation_package,
        )
        from conformance.test_tier4_validation_library import (
            claim_lifecycle_status, register_validation_package,
        )
        from audience_lab.dashboard import TIER4_VALIDATION_SECTION_HTML

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            run_dir = copy_fixture(root, COPY_ONLY_FIXTURE)
            attach_research_backed_audience(run_dir)
            panel = run_dir / "audience" / "snapshot" / "audience-panel-package.zip"
            inputs = Tier4ValidationPackageTests()._inputs(root / "tier4-inputs", panel)
            package = build_validation_package(
                inputs=inputs, panel_package_path=panel, output_dir=run_dir / "validation",
            )
            validated = validate_validation_package(package)
            library_root = run_dir / "validation-library"
            register_validation_package(
                package,
                library_root=library_root,
                registered_at="2026-09-02T00:00:00Z",
            )
            manifest_path = run_dir / "study-manifest.json"
            manifest = read_json(manifest_path)
            manifest["validation_package"] = {
                "package_path": "validation/audience-panel-validation-package.zip",
                "package_zip_sha256": validated["package_zip_sha256"],
                "package_manifest_sha256": validated["package_manifest_sha256"],
                "claim_id": validated["claim_id"],
                "claim_sha256": validated["claim_sha256"],
                "claim_scope_sha256": validated["claim_scope_sha256"],
                "panel_id": validated["panel_binding"]["panel_id"],
                "panel_version": validated["panel_binding"]["panel_version"],
                "library_path": "validation-library",
            }
            write_json(manifest_path, manifest)
            output = root / "dashboard.html"
            class October2026(datetime):
                @classmethod
                def now(cls, tz=None):
                    value = cls(2026, 10, 1, tzinfo=timezone.utc)
                    return value if tz is None else value.astimezone(tz)

            with patch("audience_lab.dashboard.datetime", October2026):
                render_to(run_dir, output)
            html = output.read_text(encoding="utf-8")
            self.assertEqual(1, html.count('id="held-out-ordering-validation"'))
            tier4_payload = payload_from(html)["tier4_validation"]
            self.assertEqual(
                "Held-out ordering validation", tier4_payload["headline"],
            )
            self.assertTrue(tier4_payload["active_claim"])
            self.assertEqual(12, tier4_payload["qualifying_block_count"])
            self.assertEqual(
                "complete_exposure_ordering",
                tier4_payload["synthetic_binding"]["surface"],
            )
            self.assertEqual(
                "qualified-response-rate", tier4_payload["metric"]["name"],
            )
            self.assertTrue(tier4_payload["segment_result"])
            self.assertEqual(
                "all_leave_outs_meet_registered_point_and_raw_p_thresholds",
                tier4_payload["influence_diagnostics"]["status"],
            )
            self.assertEqual(
                12,
                len(
                    tier4_payload["influence_diagnostics"][
                        "leave_one_block"
                    ]
                ),
            )
            self.assertTrue(tier4_payload["refresh_triggers"])
            validated_dashboard = validator_result(output)
            self.assertEqual(
                0,
                validated_dashboard.returncode,
                validated_dashboard.stdout + validated_dashboard.stderr,
            )

            output.write_text(html.replace(TIER4_VALIDATION_SECTION_HTML, "", 1), encoding="utf-8")
            removed = validator_result(output)
            self.assertNotEqual(0, removed.returncode)
            self.assertIn("Held-out ordering validation section", removed.stdout)
            output.write_text(html, encoding="utf-8")

            manifest["validation_package"]["claim_sha256"] = "sha256:" + "0" * 64
            write_json(manifest_path, manifest)
            with self.assertRaisesRegex(DashboardInputError, "does not match authenticated claim"):
                render_to(run_dir, root / "binding-mismatch.html")
            manifest["validation_package"]["claim_sha256"] = validated["claim_sha256"]
            write_json(manifest_path, manifest)
            active_state = claim_lifecycle_status(
                validated["claim_id"],
                library_root=library_root,
                as_of="2026-10-01T00:00:00Z",
            )
            for lifecycle_status in (
                "expired", "superseded", "withdrawn", "invalidated",
            ):
                state = {**active_state, "lifecycle_status": lifecycle_status}
                with self.subTest(lifecycle_status=lifecycle_status), patch(
                    "audience_panel_builder.population.validation.library.claim_lifecycle_status",
                    return_value=state,
                ), patch("audience_lab.dashboard.datetime", October2026):
                    inactive_output = root / f"{lifecycle_status}.html"
                    render_to(run_dir, inactive_output)
                    tier4 = payload_from(
                        inactive_output.read_text(encoding="utf-8")
                    )["tier4_validation"]
                    self.assertFalse(tier4["active_claim"])
                    self.assertEqual(lifecycle_status, tier4["claim_status"])
                    self.assertNotEqual(
                        "Held-out ordering validation", tier4["headline"],
                    )

            with patch(
                "audience_panel_builder.population.validation.library.current_claim",
                return_value={
                    "claim": {
                        "claim_id": "different-same-scope-claim",
                    },
                },
            ), patch("audience_lab.dashboard.datetime", October2026):
                dormant_output = root / "registered-not-current.html"
                render_to(run_dir, dormant_output)
            dormant = payload_from(
                dormant_output.read_text(encoding="utf-8")
            )["tier4_validation"]
            self.assertFalse(dormant["active_claim"])
            self.assertEqual("not_current", dormant["claim_status"])

            class March2027(datetime):
                @classmethod
                def now(cls, tz=None):
                    value = cls(2027, 3, 1, tzinfo=timezone.utc)
                    return value if tz is None else value.astimezone(tz)

            with patch("audience_lab.dashboard.datetime", March2027):
                expired_output = root / "actual-expired.html"
                render_to(run_dir, expired_output)
            expired_payload = payload_from(
                expired_output.read_text(encoding="utf-8")
            )["tier4_validation"]
            self.assertFalse(expired_payload["active_claim"])
            self.assertEqual("expired", expired_payload["claim_status"])

    def test_authenticated_negative_validation_renders_without_an_active_badge(self):
        from conformance.test_tier4_validation_package import Tier4ValidationPackageTests
        from conformance.test_tier4_validation_package import (
            build_validation_package, validate_validation_package,
        )

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            run_dir = copy_fixture(root, COPY_ONLY_FIXTURE)
            attach_research_backed_audience(run_dir)
            panel = run_dir / "audience" / "snapshot" / "audience-panel-package.zip"
            package = build_validation_package(
                inputs=Tier4ValidationPackageTests()._inputs(
                    root / "tier4-negative-inputs", panel, negative=True,
                ),
                panel_package_path=panel,
                output_dir=run_dir / "validation",
            )
            validated = validate_validation_package(package)
            manifest_path = run_dir / "study-manifest.json"
            manifest = read_json(manifest_path)
            manifest["validation_package"] = {
                "package_path": "validation/audience-panel-validation-package.zip",
                "package_zip_sha256": validated["package_zip_sha256"],
                "package_manifest_sha256": validated["package_manifest_sha256"],
                "claim_id": None,
                "claim_sha256": None,
                "claim_scope_sha256": validated["claim_scope_sha256"],
                "panel_id": validated["panel_binding"]["panel_id"],
                "panel_version": validated["panel_binding"]["panel_version"],
                "library_path": None,
            }
            write_json(manifest_path, manifest)
            output = root / "negative-dashboard.html"
            render_to(run_dir, output)
            tier4 = payload_from(
                output.read_text(encoding="utf-8")
            )["tier4_validation"]
            self.assertFalse(tier4["active_claim"])
            self.assertEqual("not_issued", tier4["claim_status"])
            self.assertEqual(
                "The result did not support Tier 4", tier4["headline"],
            )
            self.assertEqual(12, tier4["qualifying_block_count"])
            self.assertEqual(
                "qualified-response-rate", tier4["metric"]["name"],
            )
            self.assertEqual(
                "one_or_more_leave_outs_do_not_meet_registered_point_and_raw_p_thresholds",
                tier4["influence_diagnostics"]["status"],
            )
            self.assertEqual([], tier4["refresh_triggers"])

    def test_tier4_surface_requires_an_authenticated_manifest_binding(self):
        html = render_fixture_dashboard()
        self.assertNotIn("Held-out ordering validation", html)
        self.assertNotIn("tier4_validation", payload_from(html))

        with tempfile.TemporaryDirectory() as temporary:
            run_dir = copy_fixture(Path(temporary))
            manifest_path = run_dir / "study-manifest.json"
            manifest = read_json(manifest_path)
            manifest["validation_package"] = {}
            write_json(manifest_path, manifest)
            with self.assertRaisesRegex(DashboardInputError, "validation_package keys are invalid"):
                render_to(run_dir, Path(temporary) / "dashboard.html")


if __name__ == "__main__":
    unittest.main()
