#!/usr/bin/env python3
"""Run the production large-library CLIs as one deterministic conformance study."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import html
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from typing import Any, Mapping, Sequence

ROOT = Path(__file__).resolve().parents[1]
SKILL_ROOT = ROOT / "skills" / "audience-ad-testing-lab"
for runtime_path in (ROOT, SKILL_ROOT / "scripts"):
    resolved = str(runtime_path)
    if resolved not in sys.path:
        sys.path.insert(0, resolved)

from conformance.test_progressive_workflow import run_workflow  # noqa: E402
from audience_lab.audience_library import (  # noqa: E402
    find_package,
    register_package,
    resolve_audience_panel,
    resolve_library_root,
)
from audience_lab.audience_package import (  # noqa: E402
    build_audience_package,
    validate_package_archive,
)
from audience_lab.audience_package_v3 import (  # noqa: E402
    build_audience_package_v3,
    validate_package_archive_v3,
)
from audience_lab.audience_resolution_v3 import resolve_audience_v3  # noqa: E402


FIXTURES = ROOT / "conformance" / "fixtures" / "e2e-large"
V3_PACKAGE_FIXTURE = (
    ROOT
    / "conformance"
    / "fixtures"
    / "audience-package-v3"
    / "approved-package-inputs.json"
)
CREATIVE_IDS = tuple(f"V{index}" for index in range(1, 8))
STRENGTH = {"V1": 7, "V2": 6, "V3": 5, "V4": 5, "V5": 3, "V6": 2, "V7": 1}
LIMITATIONS = [
    "Predicted visual attention is not eye tracking or respondent preference.",
    "The overlay does not estimate thumb-stop, click-through, conversion, or revenue impact.",
]


def _read_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise RuntimeError(f"{path} must contain an object")
    return payload


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, indent=2, sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8",
    )


def _write_jsonl(path: Path, records: Sequence[Mapping[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(
            json.dumps(record, sort_keys=True, separators=(",", ":"), allow_nan=False)
            + "\n"
            for record in records
        ),
        encoding="utf-8",
    )


def _run(*arguments: str) -> subprocess.CompletedProcess[str]:
    completed = subprocess.run(
        [sys.executable, *arguments],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode:
        raise RuntimeError(
            f"command failed ({completed.returncode}): {' '.join(arguments)}\n"
            f"stdout:\n{completed.stdout}\nstderr:\n{completed.stderr}"
        )
    return completed


def _sha256_bytes(value: bytes) -> str:
    return "sha256:" + hashlib.sha256(value).hexdigest()


def _svg(creative_id: str, *, overlay: bool = False) -> bytes:
    index = int(creative_id[1:])
    title = {
        "V1": "Proof before promise",
        "V2": "Give the team Tuesday back",
        "V3": "One view of work in motion",
        "V4": "Make ownership unmistakable",
        "V5": "Move from brief to launch faster",
        "V6": "Automation across the workflow",
        "V7": "Transform work everywhere",
    }[creative_id]
    accent = ("#f97316", "#14b8a6", "#4f46e5", "#8b5cf6", "#0ea5e9", "#64748b", "#334155")[index - 1]
    heat = (
        '<radialGradient id="heat"><stop offset="0" stop-color="#ffef5a" stop-opacity=".9"/>'
        '<stop offset=".45" stop-color="#ff6b35" stop-opacity=".6"/>'
        '<stop offset="1" stop-color="#ef4444" stop-opacity="0"/></radialGradient>'
        if overlay
        else ""
    )
    heat_shapes = (
        '<ellipse cx="600" cy="240" rx="245" ry="155" fill="url(#heat)"/>'
        '<ellipse cx="780" cy="610" rx="190" ry="125" fill="url(#heat)"/>'
        if overlay
        else ""
    )
    source = f'''<svg xmlns="http://www.w3.org/2000/svg" width="1200" height="750" viewBox="0 0 1200 750">
<defs>{heat}</defs><rect width="1200" height="750" fill="#f8fafc"/><rect x="54" y="54" width="1092" height="642" rx="30" fill="#fff" stroke="#dbe4f0" stroke-width="3"/>
<rect x="54" y="54" width="18" height="642" rx="9" fill="{accent}"/><text x="112" y="126" font-family="Arial" font-size="26" font-weight="700" fill="#172033">ACME OPERATIONS</text>
<text x="112" y="250" font-family="Arial" font-size="54" font-weight="700" fill="#172033">{html.escape(title)}</text>
<text x="112" y="320" font-family="Arial" font-size="27" fill="#475569">A concrete operating signal for teams evaluating change.</text>
<rect x="112" y="410" width="560" height="14" rx="7" fill="#dbe4f0"/><rect x="112" y="454" width="465" height="14" rx="7" fill="#dbe4f0"/><rect x="112" y="498" width="510" height="14" rx="7" fill="#dbe4f0"/>
<rect x="112" y="578" width="310" height="74" rx="14" fill="{accent}"/><text x="267" y="625" text-anchor="middle" font-family="Arial" font-size="24" font-weight="700" fill="#fff">See the operating model</text>
<circle cx="940" cy="430" r="150" fill="{accent}" opacity=".12"/><path d="M790 430h300M940 280v300" stroke="{accent}" stroke-width="18" stroke-linecap="round" opacity=".75"/>{heat_shapes}</svg>'''
    return source.encode("utf-8")


def _manifest(plan: Mapping[str, Any], media_hashes: Mapping[str, str]) -> dict[str, Any]:
    assignment = plan["assignment"]
    return {
        "study_id": plan["study_id"],
        "study_version": "task-9-e2e-v1",
        "study_objective": "Identify three ads for a deeper finalist review without overstating synthetic evidence.",
        "creative_format": plan["creative_format"],
        "method": plan["method"],
        "requested_shortlist_size": plan["requested_shortlist_size"],
        "maximum_synthetic_panelists": plan["synthetic_replicate_capacity"]["ceiling"],
        "synthetic_replicate_capacity": plan["synthetic_replicate_capacity"],
        "audience_lock": plan["audience_lock"],
        "audience_package": plan["audience_package"],
        "assignment": {
            "block_size": 4,
            "randomization_seed": str(assignment["seed"]),
            "instantiation_seed": "task-9-e2e-replicates-v1",
            "assignment_version": assignment["assignment_version"],
            "planned_participations_per_creative": 9,
            "usable_participations_per_creative": assignment["exposure_counts"],
        },
        "model": {
            "maxdiff_version": "joint-maxdiff-v1",
            "penalty_type": "l2",
            "penalty_lambda": 0.1,
            "optimizer_tolerance": 0.000001,
            "bootstrap_count": 2000,
            "successful_bootstrap_rate": 0.98,
            "clear_finalist_threshold": 0.9,
            "clear_non_finalist_threshold": 0.1,
            "pairwise_model": "davidson",
            "pairwise_tie_parameter": 0.2,
            "pairwise_penalty_lambda": 0.1,
            "pairwise_optimizer_tolerance": 0.000001,
        },
        "runtime": {
            "orchestration_mode": "context_isolated_workers",
            "provider": "conformance-synthetic-provider",
            "model_revision": "deterministic-task-9-fixture",
            "decoding_parameters": {"temperature": 0.0, "top_p": 1.0},
            "prompt_contract_version": "progressive-response-v1",
            "rendered_prompt_hashes": ["sha256:" + "2" * 64],
            "code_commit": "task-9-e2e",
            "worker_context_isolation": "isolated",
            "retry_limit_per_return": 1,
        },
        "outputs": {"creative_asset_hashes": dict(media_hashes)},
        "external_validity": {
            "human_alignment_validation": "not_evaluated",
            "field_performance_calibration": "none",
        },
        "validity_status": "valid",
        "validity_reasons": [],
    }


def _base_response(job: Mapping[str, Any]) -> tuple[dict[str, Any], list[dict[str, Any]], str]:
    reaction_copy = {
        "V1": {
            "immediate": "The before-and-after proof makes the operating benefit easy to understand.",
            "noticed": "The proof point beside the promise.",
            "positive": "The mechanism gives the claim a concrete reason to believe.",
            "negative": "The workflow-review call to action may feel heavier than the quick benefit.",
        },
        "V2": {
            "immediate": "Getting capacity back feels useful, but the size of the gain needs support.",
            "noticed": "The recovered-capacity promise.",
            "positive": "The benefit is framed in practical team terms.",
            "negative": "No concrete proof explains how the capacity is recovered.",
        },
        "V3": {
            "immediate": "A shared operating picture makes the coordination problem recognizable.",
            "noticed": "The single-view operating mechanism.",
            "positive": "The visual and copy point to the same coordination benefit.",
            "negative": "The claim does not say which handoff improves first.",
        },
        "V4": {
            "immediate": "Clear ownership is relevant, though the wording feels process-heavy.",
            "noticed": "The ownership promise.",
            "positive": "The ad names a familiar source of operating friction.",
            "negative": "The message needs a more concrete example of what becomes easier.",
        },
        "V5": {
            "immediate": "Faster launch is easy to value, but the ad leaves the acceleration mechanism vague.",
            "noticed": "The speed-to-launch headline.",
            "positive": "The outcome is immediate and easy to scan.",
            "negative": "The claim lacks a visible reason the launch would move faster.",
        },
        "V6": {
            "immediate": "Workflow automation sounds efficient but broad without a named task.",
            "noticed": "The automation promise.",
            "positive": "The message connects directly to operational effort.",
            "negative": "The scope is too wide to picture the first useful change.",
        },
        "V7": {
            "immediate": "Broad transformation sounds ambitious but is difficult to evaluate.",
            "noticed": "The transformation claim.",
            "positive": "The ambition is visible immediately.",
            "negative": "The ad does not identify a concrete starting point or proof mechanism.",
        },
    }
    attempts: list[dict[str, Any]] = []
    reactions: list[dict[str, Any]] = []
    for position, creative_id in enumerate(job["shown_order"], 1):
        copy = reaction_copy[creative_id]
        provider_id = f"{job['response_id']}-reaction-{position}-attempt-1"
        attempts.append(
            {
                "attempt_id": provider_id,
                "stage": "reaction",
                "position_seen": position,
                "attempt_number": 1,
                "provider_return_id": provider_id,
                "outcome": "accepted",
                "validation_errors": [],
            }
        )
        reactions.append(
            {
                "reaction_id": f"{job['response_id']}-reaction-{position}",
                "variation_id": creative_id,
                "display_label_seen": job["blind_labels"][creative_id],
                "position_seen": position,
                "reaction_label": "immediate",
                "immediate_reaction": copy["immediate"],
                "judgment_status": "judged",
                "noticed_or_understood_first": copy["noticed"],
                "strongest_positive_signal": copy["positive"],
                "strongest_negative_signal": copy["negative"],
                "source_provenance": {
                    "provider_return_id": provider_id,
                    "capture": "verbatim_provider_return",
                },
            }
        )
    comparison_id = f"{job['response_id']}-comparison-attempt-1"
    attempts.append(
        {
            "attempt_id": comparison_id,
            "stage": "comparison",
            "attempt_number": 1,
            "provider_return_id": comparison_id,
            "outcome": "accepted",
            "validation_errors": [],
        }
    )
    response = {
        "study_id": job["study_id"],
        "response_id": job["response_id"],
        "record_type": job["record_type"],
        "method": job["method"],
        "synthetic_replicate_id": job["synthetic_replicate_id"],
        "reviewer_dispatch_id": job["dispatch_id"],
        "persona_archetype_id": job["persona_archetype_id"],
        "segment_id": job["segment_id"],
        "profile_snapshot": job["profile_snapshot"],
        "context_attribute_provenance": job["context_attribute_provenance"],
        "worker_context_isolation": job["worker_context_isolation"],
        "human_sample_independence": False,
        "assigned_variation_ids": job["variation_ids"],
        "blind_labels": job["blind_labels"],
        "shown_order": job["shown_order"],
        "reaction_protocol": job["reaction_protocol"],
        "runtime_attempts": attempts,
        "validation": {
            "schema_valid": True,
            "assignment_valid": True,
            "reaction_order_valid": True,
        },
    }
    if "context_stratum_id" in job:
        response["context_stratum_id"] = job["context_stratum_id"]
    return response, reactions, comparison_id


def _screening_response(job: Mapping[str, Any], index: int) -> dict[str, Any]:
    response, reactions, comparison_id = _base_response(job)
    assigned = list(job["variation_ids"])
    screening_strength = {**STRENGTH, "V4": STRENGTH["V3"]}
    best_score = max(screening_strength[item] for item in assigned)
    best_candidates = [item for item in assigned if screening_strength[item] == best_score]
    best = best_candidates[index % len(best_candidates)]
    weakest_score = min(screening_strength[item] for item in assigned)
    weakest_candidates = [
        item for item in assigned if screening_strength[item] == weakest_score
    ]
    weakest = weakest_candidates[index % len(weakest_candidates)]
    if {"V3", "V4"}.issubset(assigned):
        v4_preferred_replicates = {
            "operations-leaders-replicate-0011",
            "transformation-leaders-replicate-0008",
            "transformation-leaders-replicate-0011",
        }
        best = (
            "V4"
            if job["synthetic_replicate_id"] in v4_preferred_replicates
            else "V3"
        )
        weakest = "V3" if best == "V4" else "V4"
    response.update(
        {
            "per_creative_reactions": reactions,
            "comparative_choice": {
                "status": "best_worst",
                "best_variation_id": best,
                "weakest_variation_id": weakest,
                "best_reason": "This option is the most specific and credible in the assigned set.",
                "weakest_reason": "This option is the least concrete in the assigned set.",
                "frozen_reaction_ids": [item["reaction_id"] for item in reactions],
                "source_provenance": {
                    "provider_return_id": comparison_id,
                    "capture": "verbatim_provider_return",
                },
            },
            "usable_maxdiff_block": True,
        }
    )
    return response


def _boundary_response(job: Mapping[str, Any]) -> dict[str, Any]:
    response, reactions, comparison_id = _base_response(job)
    shown = list(job["shown_order"])
    preferred = "V3"
    response.update(
        {
            "pair_assignment_id": job["pair_assignment_id"],
            "boundary_wave": job["boundary_wave"],
            "per_creative_reactions": reactions,
            "pairwise_choice": {
                "status": "first_preferred" if shown[0] == preferred else "second_preferred",
                "preferred_variation_id": preferred,
                "reason": "V3 makes the operating mechanism more concrete.",
                "frozen_reaction_ids": [item["reaction_id"] for item in reactions],
                "source_provenance": {
                    "provider_return_id": comparison_id,
                    "capture": "verbatim_provider_return",
                },
            },
            "usable_pairwise_observation": True,
        }
    )
    return response


def _finalist_response(job: Mapping[str, Any], index: int) -> dict[str, Any]:
    response, reactions, comparison_id = _base_response(job)
    winners = ("V1", "V1", "V1", "V1", "V1", "V2", "V2", "V3")
    winner = winners[index]
    ranking = sorted(job["variation_ids"], key=lambda item: (item != winner, -STRENGTH[item], item))
    reviews: list[dict[str, Any]] = []
    finalist_feedback = {
        "V1": ["Keep the proof point and test a lighter call to action."],
        "V2": ["Keep the practical time benefit and test one concrete proof mechanism."],
        "V3": ["Keep the shared-view mechanism and test a specific first handoff."],
    }
    for reaction in reactions:
        creative_id = reaction["variation_id"]
        score = 5 if creative_id == winner else 4 if creative_id == "V1" else 3
        reviews.append(
            {
                **reaction,
                "rubric_scores": {
                    key: score
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
                },
                "feedback": finalist_feedback.get(
                    creative_id,
                    ["Keep the clearest benefit and test one more specific proof point."],
                ),
                "rubric_source_provenance": {
                    "provider_return_id": comparison_id,
                    "capture": "verbatim_provider_return",
                },
            }
        )
        for key in (
            "noticed_or_understood_first",
            "strongest_positive_signal",
            "strongest_negative_signal",
        ):
            reviews[-1].pop(key)
    response.update(
        {
            "finalist_reviews": reviews,
            "final_preference_ranking": ranking,
        }
    )
    return response


def _assignment_core(
    study_id: str, method: str, jobs: Sequence[Mapping[str, Any]]
) -> dict[str, Any]:
    return {
        "study_id": study_id,
        "method": method,
        "assignment": {"synthetic_replicate_jobs": list(jobs)},
    }


def _dispatch_context(record_type: str, study_id: str) -> dict[str, Any]:
    context = _read_json(FIXTURES / "dispatch-context.json")
    context["study_id"] = study_id
    context["record_type"] = record_type
    return context


def _raw_returns(responses: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for response in responses:
        for attempt in response["runtime_attempts"]:
            record = {
                "provider_return_id": attempt["provider_return_id"],
                "synthetic_replicate_id": response["synthetic_replicate_id"],
                "reviewer_dispatch_id": response["reviewer_dispatch_id"],
                "stage": attempt["stage"],
                "attempt_number": attempt["attempt_number"],
                "accepted": attempt["outcome"] == "accepted",
                "validation_errors": attempt["validation_errors"],
                "raw_return": {
                    "fixture": "deterministic conformance provider return",
                    "response_id": response["response_id"],
                },
            }
            if attempt["stage"] == "reaction":
                record["position_seen"] = attempt["position_seen"]
            records.append(record)
    return records


def _dispatch_audit_for_responses(
    responses: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    return [
        {
            "record_type": response["record_type"],
            "synthetic_replicate_id": response["synthetic_replicate_id"],
            "reviewer_dispatch_id": response["reviewer_dispatch_id"],
            "accepted": True,
            "attempt_contract": {
                "retry_limit_per_return": 1,
                "reaction_positions": list(
                    range(1, len(response["shown_order"]) + 1)
                ),
                "comparison_required": True,
            },
            "reaction_attempts": [
                sum(
                    attempt["stage"] == "reaction"
                    and attempt.get("position_seen") == position
                    for attempt in response["runtime_attempts"]
                )
                for position in range(1, len(response["shown_order"]) + 1)
            ],
            "comparison_attempts": sum(
                attempt["stage"] == "comparison"
                for attempt in response["runtime_attempts"]
            ),
        }
        for response in responses
    ]


def _roster_and_saliency(
    run_dir: Path, study_id: str
) -> tuple[dict[str, Any], dict[str, Any]]:
    media_dir = run_dir / "media"
    media_dir.mkdir(parents=True, exist_ok=True)
    creatives: list[dict[str, Any]] = []
    entries: list[dict[str, Any]] = []
    for creative_id in CREATIVE_IDS:
        original = _svg(creative_id)
        overlay = _svg(creative_id, overlay=True)
        original_path = media_dir / f"{creative_id}.svg"
        overlay_path = media_dir / f"{creative_id}-overlay.svg"
        original_path.write_bytes(original)
        overlay_path.write_bytes(overlay)
        content_hash = _sha256_bytes(original)
        representation_id = f"{creative_id}-static-01"
        creatives.append(
            {
                "variation_id": creative_id,
                "display_name": {
                    "V1": "Proof before promise",
                    "V2": "Capacity recovered",
                    "V3": "Shared operating picture",
                    "V4": "Ownership clarity",
                    "V5": "Speed to launch",
                    "V6": "Workflow automation",
                    "V7": "Broad transformation",
                }[creative_id],
                "format": "static image",
                "headline": f"{creative_id}: a concrete operating promise",
                "body": "A grounded claim with an inspectable mechanism and clear next step.",
                "cta": "See the operating model",
                "visual_description": "A restrained operating diagram with one focal proof point.",
                "input_fidelity": "supplied asset",
                "media": [
                    {
                        "representation_id": representation_id,
                        "content_hash": content_hash,
                        "kind": "image",
                        "path": f"media/{creative_id}.svg",
                        "alt": f"{creative_id} static creative",
                        "label": "Supplied static creative",
                    }
                ],
            }
        )
        entries.append(
            {
                "variation_id": creative_id,
                "representation_id": representation_id,
                "content_hash": content_hash,
                "original_path": f"media/{creative_id}.svg",
                "overlay_path": f"media/{creative_id}-overlay.svg",
                "overlay_content_hash": _sha256_bytes(overlay),
                "predeclared_target": "Headline, proof mechanism, and call to action",
                "target_declared_at": "2026-07-22T12:01:00Z",
                "categorical_alignment": "aligned" if creative_id in {"V1", "V3"} else "partially_aligned",
                "provider": "SUM",
                "limitations": LIMITATIONS,
            }
        )
    return (
        {"study_id": study_id, "creatives": creatives},
        {
            "study_id": study_id,
            "status": "available",
            "provider": "SUM",
            "method": "computational saliency",
            "revealed_at": "2026-07-22T12:02:00Z",
            "entries": entries,
        },
    )


def _feedback(
    responses: Sequence[Mapping[str, Any]], study_id: str
) -> dict[str, Any]:
    themes: list[dict[str, Any]] = []
    insights = {
        "V1": (
            "The proof mechanism made the operating benefit concrete.",
            "A visible reason to believe gives the marketer a message element worth preserving.",
            "The workflow-review call to action may feel heavier than the quick benefit.",
            "A heavier next step may introduce hesitation that the marketer should isolate in a comparison.",
            "Test the current call to action against a lighter, specific next step while holding the proof-led message fixed.",
        ),
        "V2": (
            "The recovered-capacity promise translated the benefit into practical team terms.",
            "A practical benefit gives the marketer a clear anchor for the next creative iteration.",
            "The ad did not show how the promised capacity would be recovered.",
            "Without a mechanism, the marketer cannot tell whether proof would make the benefit easier to trust.",
            "Test the current version against one version with a single concrete proof mechanism.",
        ),
        "V3": (
            "The shared operating picture made the coordination problem recognizable.",
            "The aligned visual and message give the marketer a coherent core idea to retain.",
            "The ad did not identify which handoff would improve first.",
            "A specific first change could help the marketer make the broad coordination benefit easier to picture.",
            "Test the current shared-view message against one version naming the first handoff improved.",
        ),
        "V4": (
            "The ownership promise named a familiar source of operating friction.",
            "A recognizable problem gives the marketer a useful starting point for a more concrete message.",
            "The process-heavy wording did not show what becomes easier in practice.",
            "The marketer needs a concrete example before deciding whether the ownership idea is worth another round.",
            "Test one version with a specific ownership handoff example against the current wording.",
        ),
        "V5": (
            "The speed-to-launch outcome was immediate and easy to scan.",
            "A quickly understood outcome gives the marketer a clear element to preserve.",
            "The ad left the acceleration mechanism vague.",
            "The marketer cannot tell whether the speed claim becomes more credible when the mechanism is visible.",
            "Test the current headline against one version that names the source of the faster launch.",
        ),
        "V6": (
            "The automation message connected directly to operational effort.",
            "The relevance of the problem gives the marketer a viable message territory to narrow.",
            "The scope was too broad to picture the first automated task.",
            "The marketer needs a more specific use case to learn whether the automation idea is worth advancing.",
            "Test a version naming one automated task against the current broad workflow claim.",
        ),
        "V7": (
            "The transformation ambition was visible immediately.",
            "The marketer can preserve the sense of ambition while testing a more concrete entry point.",
            "The ad lacked a starting point or proof mechanism.",
            "Without a first step, the marketer has little guidance for turning the broad promise into a credible revision.",
            "Test a narrower version with one starting point against the current transformation promise.",
        ),
    }

    screening = [response for response in responses if response["record_type"] == "screening_response"]
    segment_ids = sorted({response["segment_id"] for response in screening})
    for creative_id, (strength, strength_why, friction, friction_why, next_test) in insights.items():
        for segment_id in segment_ids:
            matches = [
                response
                for response in screening
                if response["segment_id"] == segment_id
                and creative_id in response["assigned_variation_ids"]
            ]
            if not matches:
                continue
            support_ids = [response["response_id"] for response in matches[:3]]
            base = {
                "count": len(matches),
                "label": f"{len(matches)} eligible accepted first-round responses in this segment",
            }
            for feedback_type, theme, why_it_matters, action in (
                (
                    "strength",
                    strength,
                    strength_why,
                    "Keep this element unchanged in the next comparison so its contribution remains interpretable.",
                ),
                ("friction", friction, friction_why, next_test),
            ):
                themes.append(
                    {
                        "stage": "screening",
                        "creative_id": creative_id,
                        "segment_id": segment_id,
                        "lane": "combined",
                        "feedback_type": feedback_type,
                        "evidence_scope": "cross_response_pattern",
                        "theme": theme,
                        "why_it_matters": why_it_matters,
                        "recommended_action": action,
                        "source_type": "model-generated synthesis",
                        "response_ids": support_ids,
                        "exposed_base": base,
                        "limitations": [
                            "Pattern is limited to accepted synthetic responses in this run and is not customer or population evidence."
                        ],
                    }
                )
            finalist_matches = [
                response
                for response in responses
                if response["record_type"] == "finalist_response"
                and response["segment_id"] == segment_id
                and creative_id in response["assigned_variation_ids"]
            ]
            if not finalist_matches:
                continue
            themes.append(
                {
                    "stage": "finalist",
                    "creative_id": creative_id,
                    "segment_id": segment_id,
                    "lane": "combined",
                    "feedback_type": "next_test",
                    "evidence_scope": "cross_response_pattern",
                    "theme": "The next comparison should isolate the main question raised by this ad's feedback.",
                    "why_it_matters": "Changing one element at a time gives the marketer a cleaner learning than a full redesign.",
                    "recommended_action": next_test,
                    "source_type": "model-generated synthesis",
                    "response_ids": [response["response_id"] for response in finalist_matches[:3]],
                    "exposed_base": {
                        "count": len(finalist_matches),
                        "label": f"{len(finalist_matches)} eligible accepted closer-review responses in this segment",
                    },
                    "limitations": [
                        "This is a synthetic test recommendation, not proof that the proposed revision will improve performance."
                    ],
                }
            )
    return {"study_id": study_id, "themes": themes}


def _execute_study(
    run_dir: Path,
    *,
    study_id: str,
    panel_id: str,
    panel_version: str,
    library_root: Path,
) -> dict[str, Any]:
    build_dir = run_dir / "_e2e-build"
    build_dir.mkdir(parents=True, exist_ok=True)
    panel = _read_json(FIXTURES / "saved-audience-panel.json")
    scope = {
        key: panel["audience_scope"][key]
        for key in (
            "audience", "market", "geography", "category", "buying_context", "exclusions",
        )
    }
    resolve_audience_panel(
        {"source": "library", "panel_id": panel_id, "version": panel_version},
        scope,
        run_dir=run_dir,
        library_root=library_root,
        now=datetime(2026, 7, 22, tzinfo=timezone.utc),
    )
    resolution_path = run_dir / "audience" / "resolution.json"
    study_request = _read_json(FIXTURES / "study-request.json")
    study_request["study_id"] = study_id
    study_request["maximum_synthetic_panelists"] = 56
    study_request["audience_panel"] = {
        "source": "library",
        "panel_id": panel_id,
        "version": panel_version,
    }
    study_request_path = build_dir / "study-request.json"
    _write_json(study_request_path, study_request)
    plan_path = run_dir / "plan.json"
    _run(
        "skills/audience-ad-testing-lab/scripts/plan-large-library.py",
        str(study_request_path),
        str(plan_path),
        "--burden-pilot",
        "passed",
        "--reported-segments",
        "2",
        "--boundary-jobs-per-wave",
        "8",
        "--boundary-waves-max",
        "2",
        "--finalist-reserved",
        "8",
        "--assignment-seed",
        "29",
        "--audience-resolution",
        str(resolution_path),
    )
    plan = _read_json(plan_path)

    roster, saliency = _roster_and_saliency(run_dir, study_id)
    media_hashes = {
        creative["variation_id"]: creative["media"][0]["content_hash"]
        for creative in roster["creatives"]
    }
    manifest = _manifest(plan, media_hashes)
    manifest_path = build_dir / "source-manifest.json"
    _write_json(manifest_path, manifest)

    screening_jobs_path = build_dir / "screening-jobs.json"
    screening_origin_path = build_dir / "screening-jobs-origin.json"
    screening_context_path = build_dir / "screening-context.json"
    _write_json(
        screening_context_path, _dispatch_context("screening_response", study_id)
    )
    _run(
        "skills/audience-ad-testing-lab/scripts/prepare-panel-jobs.py",
        str(plan_path),
        str(screening_context_path),
        str(screening_jobs_path),
        "--audience-resolution",
        str(resolution_path),
        "--legacy-v2-origin-authority-output",
        str(screening_origin_path),
    )
    _run(
        "skills/audience-ad-testing-lab/scripts/validate-panel-run.py",
        str(screening_jobs_path),
        "--legacy-v2-origin-authority",
        str(screening_origin_path),
        "--expected-count",
        "32",
    )
    screening_jobs = _read_json(screening_jobs_path)["synthetic_replicate_jobs"]
    exhausted_job = screening_jobs[0]
    exhausted_workflow = run_workflow(
        exhaust_first_reaction=True,
        job=exhausted_job,
    )["result"]
    if (
        exhausted_workflow.get("status") != "incomplete"
        or exhausted_workflow.get("responses") != []
        or len(exhausted_workflow.get("raw_provider_returns", [])) != 5
    ):
        raise RuntimeError("production workflow did not retain the expected incomplete shape")
    screening_responses = [
        _screening_response(job, index)
        for index, job in enumerate(screening_jobs[1:], start=1)
    ]
    screening_responses_path = build_dir / "screening-responses.jsonl"
    _write_jsonl(screening_responses_path, screening_responses)
    screening_dispatch_audit_path = build_dir / "screening-dispatch-audit.jsonl"
    _write_jsonl(
        screening_dispatch_audit_path,
        [
            *_dispatch_audit_for_responses(screening_responses),
            exhausted_workflow["dispatch_audit"][0],
        ],
    )
    accepted_screening_jobs_path = build_dir / "accepted-screening-jobs.json"
    _write_json(
        accepted_screening_jobs_path,
        {"synthetic_replicate_jobs": screening_jobs[1:]},
    )
    _run(
        "skills/audience-ad-testing-lab/scripts/validate-panel-run.py",
        str(accepted_screening_jobs_path),
        "--legacy-v2-origin-authority",
        str(screening_origin_path),
        "--responses",
        str(screening_responses_path),
        "--expected-count",
        "31",
    )

    recovery = _read_json(SKILL_ROOT / "references" / "screening-recovery-config.json")
    recovery.update(
        {
            "version": "task-9-conformance-calibration-v1",
            "calibration_status": "calibrated",
            "utility_separation_band": {
                "minimum_log_utility_gap": 0.0,
                "maximum_log_utility_gap": 100.0,
            },
        }
    )
    recovery_path = build_dir / "recovery.json"
    _write_json(recovery_path, recovery)
    screening_output = run_dir / "screening-model-results.json"
    _run(
        "skills/audience-ad-testing-lab/scripts/aggregate-screening.py",
        "screening",
        "--manifest",
        str(manifest_path),
        "--jobs",
        str(screening_jobs_path),
        "--responses",
        str(screening_responses_path),
        "--dispatch-audit",
        str(screening_dispatch_audit_path),
        "--recovery-config",
        str(recovery_path),
        "--output",
        str(screening_output),
    )
    screening_bytes_before_saliency = screening_output.read_bytes()
    screening = _read_json(screening_output)
    if (
        screening.get("audience_package") != manifest["audience_package"]
        or screening.get("audience_lock") != manifest["audience_lock"]
    ):
        raise RuntimeError("screening authority did not preserve the v2 audience binding")
    if screening.get("validity_status") != "valid":
        raise RuntimeError(f"screening did not reach valid: {screening.get('validity_reasons')}")
    candidates = sorted(
        creative_id
        for creative_id, status in screening["classifications"].items()
        if status == "boundary_candidate"
    )
    if candidates != ["V3", "V4"]:
        raise RuntimeError(f"expected V3/V4 boundary, got {candidates}")
    frozen = screening.get("boundary_plan", {})
    if frozen.get("frozen_before_dispatch") is not True:
        raise RuntimeError("screening output did not freeze the boundary plan")

    authorized = frozen["predeclared_pair_assignments"]
    boundary_context_path = build_dir / "boundary-context.json"
    boundary_jobs_path = build_dir / "boundary-jobs.json"
    boundary_origin_path = build_dir / "boundary-jobs-origin.json"
    boundary_context = _dispatch_context("boundary_response", study_id)
    boundary_context["boundary_waves"] = [1]
    _write_json(boundary_context_path, boundary_context)
    _run(
        "skills/audience-ad-testing-lab/scripts/prepare-panel-jobs.py",
        str(screening_output),
        str(boundary_context_path),
        str(boundary_jobs_path),
        "--manifest",
        str(manifest_path),
        "--audience-resolution",
        str(resolution_path),
        "--legacy-v2-origin-authority-output",
        str(boundary_origin_path),
    )
    boundary_jobs = _read_json(boundary_jobs_path)["synthetic_replicate_jobs"]
    boundary_responses = [_boundary_response(job) for job in boundary_jobs]
    boundary_responses_path = build_dir / "boundary-responses.jsonl"
    _write_jsonl(boundary_responses_path, boundary_responses)
    _run(
        "skills/audience-ad-testing-lab/scripts/validate-panel-run.py",
        str(boundary_jobs_path),
        "--legacy-v2-origin-authority",
        str(boundary_origin_path),
        "--responses",
        str(boundary_responses_path),
        "--expected-count",
        "8",
    )
    boundary_output = run_dir / "boundary-results.json"
    _run(
        "skills/audience-ad-testing-lab/scripts/aggregate-screening.py",
        "boundary",
        "--manifest",
        str(manifest_path),
        "--screening-results",
        str(screening_output),
        "--responses",
        str(boundary_responses_path),
        "--output",
        str(boundary_output),
    )
    boundary = _read_json(boundary_output)
    if (
        boundary.get("audience_package") != manifest["audience_package"]
        or boundary.get("audience_lock") != manifest["audience_lock"]
    ):
        raise RuntimeError("boundary authority did not preserve the v2 audience binding")
    if boundary.get("status") != "resolved":
        raise RuntimeError(f"boundary did not resolve: {boundary.get('status_reasons')}")
    finalist_ids = list(boundary["proposed_finalist_ids"])

    approval = {
        "study_id": plan["study_id"],
        "method": plan["method"],
        "audience_lock": boundary["audience_lock"],
        "audience_package": boundary["audience_package"],
        "approved_finalist_ids": finalist_ids,
        "roster_decision": {
            "status": "approved",
            "approved_at": "2026-07-22T12:00:00Z",
            "approved_by": "study owner",
            "override": False,
            "changed_after_saliency_reveal": False,
        },
    }
    approval_path = build_dir / "approval.json"
    _write_json(approval_path, approval)
    finalist_context_path = build_dir / "finalist-context.json"
    finalist_jobs_path = build_dir / "finalist-jobs.json"
    finalist_origin_path = build_dir / "finalist-jobs-origin.json"
    finalist_context = _dispatch_context("finalist_response", study_id)
    finalist_context["requested_job_slots"] = 8
    _write_json(finalist_context_path, finalist_context)
    _run(
        "skills/audience-ad-testing-lab/scripts/prepare-panel-jobs.py",
        str(approval_path),
        str(finalist_context_path),
        str(finalist_jobs_path),
        "--manifest",
        str(manifest_path),
        "--audience-resolution",
        str(resolution_path),
        "--legacy-v2-origin-authority-output",
        str(finalist_origin_path),
    )
    finalist_jobs = _read_json(finalist_jobs_path)["synthetic_replicate_jobs"]
    finalist_responses = [
        _finalist_response(job, index) for index, job in enumerate(finalist_jobs)
    ]
    finalist_responses_path = build_dir / "finalist-responses.jsonl"
    _write_jsonl(finalist_responses_path, finalist_responses)
    _run(
        "skills/audience-ad-testing-lab/scripts/validate-panel-run.py",
        str(finalist_jobs_path),
        "--legacy-v2-origin-authority",
        str(finalist_origin_path),
        "--responses",
        str(finalist_responses_path),
        "--expected-count",
        "8",
    )
    finalist_output = run_dir / "finalist-results.json"
    _run(
        "skills/audience-ad-testing-lab/scripts/aggregate-screening.py",
        "finalists",
        "--manifest",
        str(manifest_path),
        "--screening-results",
        str(screening_output),
        "--boundary-results",
        str(boundary_output),
        "--approval",
        str(approval_path),
        "--jobs",
        str(finalist_jobs_path),
        "--responses",
        str(finalist_responses_path),
        "--output",
        str(finalist_output),
    )
    finalist = _read_json(finalist_output)
    if (
        finalist.get("audience_package") != manifest["audience_package"]
        or finalist.get("audience_lock") != manifest["audience_lock"]
        or finalist.get("audience_package") != plan["audience_package"]
        or finalist.get("audience_lock") != plan["audience_lock"]
    ):
        raise RuntimeError(
            "finalist authority did not preserve the plan and manifest v2 audience binding"
        )

    all_responses = screening_responses + boundary_responses + finalist_responses
    exhausted_raw = exhausted_workflow["raw_provider_returns"]
    exhausted_rejected = exhausted_workflow["rejected_attempts"]
    exhausted_dispatch = exhausted_workflow["dispatch_audit"][0]
    workflow = {
        "run_id": f"{study_id}-with-production-exhaustion",
        "status": exhausted_workflow["status"],
        "requested_replicates": len(all_responses) + 1,
        "completed_replicates": len(all_responses),
        "missing_synthetic_replicate_ids": exhausted_workflow[
            "missing_synthetic_replicate_ids"
        ],
        "responses": all_responses,
        "raw_provider_returns": [*_raw_returns(all_responses), *exhausted_raw],
        "rejected_attempts": exhausted_rejected,
        "dispatch_audit": [
            *_dispatch_audit_for_responses(all_responses),
            exhausted_dispatch,
        ],
        "validation_failures": exhausted_workflow["validation_failures"],
    }
    workflow_path = build_dir / "workflow.json"
    _write_json(workflow_path, workflow)
    _run(
        "skills/audience-ad-testing-lab/scripts/materialize-run-lineage.py",
        str(workflow_path),
        str(manifest_path),
        str(run_dir),
    )

    _write_json(run_dir / "creative-roster.json", roster)
    _write_json(
        run_dir / "feedback-synthesis.json", _feedback(all_responses, study_id)
    )
    proposal_before_saliency = list(boundary["proposed_finalist_ids"])
    _write_json(run_dir / "saliency-index.json", saliency)
    saliency_shortlist_invariant = (
        screening_output.read_bytes() == screening_bytes_before_saliency
        and _read_json(boundary_output)["proposed_finalist_ids"] == proposal_before_saliency
    )
    dashboard_path = run_dir / "dashboard.html"
    _run(
        "skills/audience-ad-testing-lab/scripts/render-dashboard.py",
        "--run-dir",
        str(run_dir),
        "--output",
        str(dashboard_path),
    )
    _run("skills/audience-ad-testing-lab/scripts/validate-dashboard.py", str(dashboard_path))

    screening_ids = {item["synthetic_replicate_id"] for item in screening_responses}
    screening_job_ids = {
        item["synthetic_replicate_id"] for item in screening_jobs
    }
    boundary_ids = {item["synthetic_replicate_id"] for item in boundary_responses}
    finalist_replicates = {item["synthetic_replicate_id"] for item in finalist_responses}
    reserve = boundary["decision_audit"]["reserve"]
    authorized_ids = {item["pair_assignment_id"] for item in authorized}
    all_exact = True
    for jobs, responses in (
        (screening_jobs[1:], screening_responses),
        (boundary_jobs, boundary_responses),
        (finalist_jobs, finalist_responses),
    ):
        for job, response in zip(jobs, responses, strict=True):
            all_exact = all_exact and (
                response["synthetic_replicate_id"] == job["synthetic_replicate_id"]
                and response["assigned_variation_ids"] == job["variation_ids"]
                and response["shown_order"] == job["shown_order"]
                and response["reviewer_dispatch_id"] == job["dispatch_id"]
            )
    all_exact = all_exact and (
        exhausted_dispatch["synthetic_replicate_id"]
        == exhausted_job["synthetic_replicate_id"]
        and exhausted_dispatch["reviewer_dispatch_id"] == exhausted_job["dispatch_id"]
        and exhausted_dispatch["attempt_contract"]["reaction_positions"]
        == list(range(1, len(exhausted_job["shown_order"]) + 1))
    )
    return {
        "study_id": plan["study_id"],
        "method": plan["method"],
        "screening_status": screening["validity_status"],
        "screening_resamples": screening["model_diagnostics"]["bootstrap"]["requested_fits"],
        "boundary_status": boundary["status"],
        "boundary_resamples": boundary["model_diagnostics"]["bootstrap"]["requested_fits"],
        "all_responses_valid_against_exact_job": all_exact,
        "boundary_plan_frozen_before_dispatch": frozen["frozen_before_dispatch"] is True,
        "only_authorized_boundary_waves_used": (
            all(item["boundary_wave"] == 1 for item in boundary_responses)
            and {item["pair_assignment_id"] for item in boundary_responses}.issubset(authorized_ids)
        ),
        "finalist_reserve_before": reserve["finalist_reserved_before"],
        "finalist_reserve_after": reserve["finalist_reserved_after"],
        "fresh_replicate_ids_across_stages": (
            not (screening_ids & boundary_ids)
            and not (screening_ids & finalist_replicates)
            and not (boundary_ids & finalist_replicates)
        ),
        "saliency_shortlist_invariant": saliency_shortlist_invariant,
        "dashboard_valid": dashboard_path.is_file(),
        "exhausted_dispatch_rendered_and_validated": True,
        "exhausted_dispatch_authorized": (
            exhausted_dispatch["synthetic_replicate_id"] in screening_job_ids
        ),
        "exhausted_workflow_status": exhausted_workflow["status"],
        "exhausted_composite_response_count": len(exhausted_workflow["responses"]),
        "exhausted_provider_call_count": len(exhausted_raw),
        "exhausted_accepted_component_call_count": sum(
            item["accepted"] for item in exhausted_raw
        ),
        "exhausted_retry_attempt_numbers": [
            item["attempt_number"]
            for item in exhausted_raw
            if item["stage"] == "reaction" and item["position_seen"] == 1
        ],
        "dashboard_path": str(dashboard_path),
        "run_dir": str(run_dir),
        "package_valid": True,
        "package_zip_sha256": manifest["audience_package"]["package_zip_sha256"],
        "package_manifest_sha256": manifest["audience_package"][
            "package_manifest_sha256"
        ],
        "panel_id": manifest["audience_package"]["panel_id"],
        "panel_version": manifest["audience_package"]["panel_version"],
        "usage": _read_json(run_dir / "study-manifest.json")["usage"],
    }


def _tree_fingerprint(path: Path) -> str:
    if not path.exists():
        return "missing"
    digest = hashlib.sha256()
    for child in sorted(path.rglob("*"), key=lambda item: str(item.relative_to(path))):
        relative = str(child.relative_to(path)).encode("utf-8")
        digest.update(relative)
        if child.is_symlink():
            digest.update(b"symlink:")
            digest.update(str(child.readlink()).encode("utf-8"))
        elif child.is_file():
            digest.update(b"file:")
            digest.update(child.read_bytes())
        else:
            digest.update(b"directory")
    return digest.hexdigest()


def _v3_reuse_proof(run_dir: Path) -> dict[str, Any]:
    """Register one v3 package and freeze distinct study allocations from it."""

    fixture = _read_json(V3_PACKAGE_FIXTURE)
    documents = fixture["bundles"]["tier_2"]
    inputs: dict[str, Path] = {}
    input_root = run_dir / "v3-research-build" / "inputs"
    input_root.mkdir(parents=True)
    for key, filename in fixture["inputs"].items():
        path = input_root / filename
        value = documents[key]
        path.write_bytes(
            value.encode("utf-8")
            if key == "report"
            else (
                json.dumps(
                    value,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                    allow_nan=False,
                )
                + "\n"
            ).encode("utf-8")
        )
        inputs[key] = path
    built = build_audience_package_v3(
        inputs=inputs,
        output_dir=run_dir / "v3-research-build" / "package",
    )
    package_bytes = built.package_zip_path.read_bytes()
    validation = validate_package_archive_v3(built.package_zip_path)
    library_root = run_dir / "temporary-v3-audience-library"
    registration = register_package(
        built.package_zip_path,
        library_root=library_root,
    )
    if registration.get("status") != "registered":
        raise RuntimeError("fresh v3 package did not register")
    panel = documents["panel"]
    registered_package = find_package(
        panel["panel_id"],
        panel["version"],
        library_root=library_root,
    )
    scope = {
        key: panel["audience_scope"][key]
        for key in (
            "audience",
            "market",
            "geography",
            "category",
            "buying_context",
            "exclusions",
        )
    }

    def plan(study_id: str) -> dict[str, Any]:
        study_root = run_dir / study_id
        envelope = resolve_audience_v3(
            package_path=registered_package,
            study_scope=scope,
            run_directory=study_root,
        )
        request = {
            "study_id": study_id,
            "creative_ids": [
                f"creative-{index}" for index in range(1, 8)
            ],
            "creative_format": "static_image",
            "requested_shortlist_size": 5,
            "maximum_synthetic_panelists": 40,
            "audience_panel": {
                "source": "file",
                "package_path": str(registered_package),
            },
        }
        request_path = study_root / "study-request.json"
        output_path = study_root / "study-plan.json"
        _write_json(request_path, request)
        _run(
            "skills/audience-ad-testing-lab/scripts/plan-large-library.py",
            str(request_path),
            str(output_path),
            "--burden-pilot",
            "passed",
            "--reported-segments",
            "1",
            "--boundary-jobs-per-wave",
            "4",
            "--boundary-waves-max",
            "2",
            "--finalist-reserved",
            "4",
            "--assignment-seed",
            "29",
            "--audience-resolution",
            str(study_root / "audience" / "resolution.json"),
        )
        result = _read_json(output_path)
        if (
            result["audience_package"]["package_zip_sha256"]
            != envelope["audience_package"]["package_zip_sha256"]
        ):
            raise RuntimeError("v3 plan did not bind the resolved package")
        return result

    first = plan("e2e-v3-reuse-001")
    second = plan("e2e-v3-reuse-002")
    first_snapshot = (
        run_dir
        / "e2e-v3-reuse-001"
        / "audience"
        / "snapshot"
        / "audience-panel-package.zip"
    ).read_bytes()
    second_snapshot = (
        run_dir
        / "e2e-v3-reuse-002"
        / "audience"
        / "snapshot"
        / "audience-panel-package.zip"
    ).read_bytes()
    return {
        "v3_panel_registered_in_temporary_library": True,
        "v3_second_study_resolved_from_library": True,
        "v3_exact_package_bytes_reused": (
            first_snapshot == second_snapshot == package_bytes
        ),
        "v3_exact_package_hash_reused": (
            first["audience_package"]["package_zip_sha256"]
            == second["audience_package"]["package_zip_sha256"]
            == validation["package_zip_sha256"]
        ),
        "v3_run_specific_immutable_allocations": (
            first["audience_profile_rosters"]["combined_sha256"]
            != second["audience_profile_rosters"]["combined_sha256"]
        ),
        "v3_first_study_worker_slots": first[
            "synthetic_replicate_capacity"
        ]["required_total"],
        "v3_second_study_worker_slots": second[
            "synthetic_replicate_capacity"
        ]["required_total"],
        "v3_package_zip_sha256": validation["package_zip_sha256"],
    }


def _build(run_dir: Path) -> dict[str, Any]:
    evidence = _read_json(FIXTURES / "research-evidence.json")
    brief = _read_json(FIXTURES / "audience-research-brief.json")
    panel = _read_json(FIXTURES / "saved-audience-panel.json")
    if evidence.get("fictional") is not True or evidence.get("public_urls_used") is not False:
        raise RuntimeError("E2E research evidence must be bundled, fictional, and URL-free")
    evidence_sources = evidence.get("sources")
    if not isinstance(evidence_sources, list) or not evidence_sources:
        raise RuntimeError("E2E research evidence packet is empty")
    local_sources = {
        str(item.get("evidence_id")): str(item.get("source_label"))
        for item in evidence_sources
        if isinstance(item, Mapping)
    }
    brief_sources = brief.get("evidence_sources")
    if not isinstance(brief_sources, list) or {
        str(item.get("evidence_id")): str(item.get("source_label"))
        for item in brief_sources
        if isinstance(item, Mapping)
    } != local_sources:
        raise RuntimeError("approved brief does not map exactly to bundled local evidence")
    if any(
        item.get("source_url") is not None
        for item in brief_sources
        if isinstance(item, Mapping)
    ):
        raise RuntimeError("bundled E2E brief must not invent public URLs")

    default_library = resolve_library_root()
    default_before = _tree_fingerprint(default_library)
    package = build_audience_package(brief, panel, run_dir / "research-build")
    package_bytes = package.package_zip_path.read_bytes()
    package_validation = validate_package_archive(package_bytes)
    library_root = run_dir / "temporary-audience-library"
    registration = register_package(
        package.package_zip_path,
        library_root=library_root,
    )
    if registration.get("status") != "registered":
        raise RuntimeError("fresh temporary audience library did not register the package")

    first = _execute_study(
        run_dir / "study-1",
        study_id="e2e-large-acme-001",
        panel_id=panel["panel_id"],
        panel_version=panel["version"],
        library_root=library_root,
    )
    second = _execute_study(
        run_dir / "study-2",
        study_id="e2e-large-acme-reuse-002",
        panel_id=panel["panel_id"],
        panel_version=panel["version"],
        library_root=library_root,
    )
    first_package = (
        Path(first["run_dir"]) / "audience" / "snapshot" / "audience-panel-package.zip"
    ).read_bytes()
    second_package = (
        Path(second["run_dir"]) / "audience" / "snapshot" / "audience-panel-package.zip"
    ).read_bytes()
    first_validation = validate_package_archive(first_package)
    second_validation = validate_package_archive(second_package)
    default_after = _tree_fingerprint(default_library)
    exact_hashes = all(
        first_validation[field]
        == second_validation[field]
        == package_validation[field]
        for field in (
            "panel_sha256",
            "brief_sha256",
            "package_manifest_sha256",
            "package_zip_sha256",
        )
    )
    v3_proof = _v3_reuse_proof(run_dir)
    return {
        **first,
        **v3_proof,
        "studies": [first, second],
        "audience_research_built_once": True,
        "audience_research_build_count": 1,
        "bundled_local_evidence_verified": True,
        "invented_public_urls": False,
        "research_brief_status": brief["status"],
        "research_approval_verified": brief["status"] == "approved",
        "panel_registered_in_temporary_library": library_root.is_relative_to(run_dir),
        "second_study_resolved_from_library": True,
        "second_study_rebuilt_research": False,
        "exact_package_bytes_reused": (
            first_package == second_package == package_bytes
        ),
        "exact_package_hashes_reused": exact_hashes,
        "real_user_library_untouched": (
            library_root.resolve() != default_library.resolve()
            and default_before == default_after
        ),
        "audience_segment_count": len(panel["segments"]),
        "audience_mindset_count": len(panel["persona_archetypes"]),
        "temporary_library_root": str(library_root),
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-report", required=True, type=Path)
    parser.add_argument("--output-run-dir", type=Path)
    args = parser.parse_args(argv)
    try:
        if args.output_run_dir:
            run_dir = args.output_run_dir.expanduser().resolve()
            if run_dir.exists():
                shutil.rmtree(run_dir)
            run_dir.mkdir(parents=True)
            report = _build(run_dir)
        else:
            with tempfile.TemporaryDirectory(prefix="audience-lab-task9-e2e-") as directory:
                report = _build(Path(directory))
        _write_json(args.output_report.expanduser().resolve(), report)
    except (OSError, UnicodeError, json.JSONDecodeError, RuntimeError, ValueError) as exc:
        print(f"large-library e2e failed: {exc}", file=sys.stderr)
        return 1
    print(f"e2e_status=passed report={args.output_report}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
