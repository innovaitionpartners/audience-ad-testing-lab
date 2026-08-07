import copy
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

from audience_lab.audience_package import (
    build_audience_package,
    read_validated_package_archive,
)
from audience_lab.responses import validate_job, validate_response


ROOT = Path(__file__).resolve().parents[1]
SKILL_ROOT = ROOT / "skills" / "audience-ad-testing-lab"
RUBRIC_KEYS = (
    "comprehension",
    "relevance",
    "credibility",
    "offer_appeal",
    "motivation",
    "friction",
    "attention_potential",
    "overall",
)


def canonical_json_bytes(value):
    def language_neutral(item):
        if isinstance(item, float) and item.is_integer():
            return int(item)
        if isinstance(item, dict):
            return {
                key: language_neutral(nested)
                for key, nested in item.items()
            }
        if isinstance(item, list):
            return [language_neutral(nested) for nested in item]
        return item

    return (
        json.dumps(
            language_neutral(value),
            indent=2,
            sort_keys=True,
            ensure_ascii=False,
            allow_nan=False,
        )
        + "\n"
    ).encode("utf-8")


def legacy_v2_record_for_job(job):
    source_assignment_core = {
        "study_id": job["study_id"],
        "method": job["method"],
        "audience_package": {
            "panel_id": "test-panel",
            "panel_version": "1.0.0",
            "panel_sha256": "1" * 64,
            "panel_byte_count": 1,
            "brief_id": "test-brief",
            "brief_sha256": "2" * 64,
            "brief_byte_count": 1,
            "package_manifest_sha256": "3" * 64,
            "package_manifest_byte_count": 1,
            "package_zip_sha256": "4" * 64,
            "package_zip_byte_count": 1,
            "resolved_snapshot_path": "audience/snapshot",
        },
    }
    source_job_core = {
        "synthetic_replicate_id": job["synthetic_replicate_id"],
        "segment_id": job["segment_id"],
        "variation_ids": job["variation_ids"],
        "shown_order": job["shown_order"],
    }
    if job["record_type"] == "screening_response":
        source_assignment_core["assignment"] = {
            "synthetic_replicate_jobs": [source_job_core]
        }
    elif job["record_type"] == "boundary_response":
        source_assignment_core["boundary_plan"] = {
            "predeclared_pair_assignments": [
                {
                    "pair_assignment_id": job["synthetic_replicate_id"],
                    "wave": job.get("boundary_wave", 1),
                    "variation_ids": job["variation_ids"],
                }
            ]
        }
    else:
        source_assignment_core["approved_finalist_ids"] = job[
            "variation_ids"
        ]
    return {
        "schema_version": "audience-jobs-producer-record-v2",
        "origin": "legacy_v2",
        "producer": "prepare-panel-jobs.py",
        "producer_version": "2.1.0",
        "source_assignment_core": source_assignment_core,
        "source_dispatch_context": {
            "study_id": job["study_id"],
            "record_type": job["record_type"],
        },
        "source_manifest": None,
        "canonical_job_cores": [job],
    }


def write_workflow_legacy_v2_evidence(root, job):
    fixtures = ROOT / "conformance/fixtures/audience-research"
    brief = json.loads(
        (fixtures / "approved-brief.json").read_text(encoding="utf-8")
    )
    panel = json.loads(
        (fixtures / "approved-panel.json").read_text(encoding="utf-8")
    )
    package = build_audience_package(brief, panel, root / "package")
    package_snapshot = read_validated_package_archive(
        package.package_zip_path
    )
    record = legacy_v2_record_for_job(job)
    package_validation = package_snapshot["validation"]
    package_binding = {
        "panel_id": package_validation["panel_id"],
        "panel_version": package_validation["panel_version"],
        "panel_sha256": package_validation["panel_sha256"],
        "panel_byte_count": len(
            package_snapshot["members"]["saved-audience-panel.json"]
        ),
        "brief_id": package_validation["brief_id"],
        "brief_sha256": package_validation["brief_sha256"],
        "brief_byte_count": len(
            package_snapshot["members"]["persona-research-brief.json"]
        ),
        "package_manifest_sha256": package_validation[
            "package_manifest_sha256"
        ],
        "package_manifest_byte_count": package_validation[
            "package_manifest_byte_count"
        ],
        "package_zip_sha256": package_validation["package_zip_sha256"],
        "package_zip_byte_count": package_validation[
            "package_zip_byte_count"
        ],
        "resolved_snapshot_path": "audience/snapshot",
    }
    record["source_assignment_core"][
        "audience_package"
    ] = package_binding
    source_context = record["source_dispatch_context"]
    produced_jobs = {
        "study_id": job["study_id"],
        "method": job["method"],
        "record_type": job["record_type"],
        "synthetic_replicate_jobs": [job],
    }
    record_path = (root / "legacy-v2-origin.json").resolve()
    evidence_directory = record_path.with_name(
        f"{record_path.stem}.evidence"
    )
    evidence_directory.mkdir()
    evidence_bytes = {
        "source_package": package_snapshot["archive_bytes"],
        "source_package_validation": canonical_json_bytes(
            package_validation
        ),
        "source_assignment": canonical_json_bytes(
            record["source_assignment_core"]
        ),
        "source_dispatch_context": canonical_json_bytes(source_context),
        "source_manifest": None,
        "produced_jobs": canonical_json_bytes(produced_jobs),
    }
    filenames = {
        "source_package": "audience-panel-package.zip",
        "source_package_validation": "source-package-validation.json",
        "source_assignment": "source-assignment.json",
        "source_dispatch_context": "source-dispatch-context.json",
        "source_manifest": "source-manifest.json",
        "produced_jobs": "produced-jobs.json",
    }
    bindings = {}
    for binding_name, filename in filenames.items():
        raw = evidence_bytes[binding_name]
        if raw is None:
            bindings[binding_name] = None
            continue
        path = evidence_directory / filename
        path.write_bytes(raw)
        path.chmod(0o400)
        bindings[binding_name] = {
            "path": f"{evidence_directory.name}/{filename}",
            "sha256": hashlib.sha256(raw).hexdigest(),
            "byte_count": len(raw),
        }
    identity = {
        binding_name: bindings[binding_name]
        for binding_name in filenames
    }
    source_context["producer_evidence"] = {
        "schema_version": "audience-jobs-producer-evidence-v1",
        "evidence_id": hashlib.sha256(
            canonical_json_bytes(identity)
        ).hexdigest(),
        **bindings,
    }
    evidence_directory.chmod(0o500)
    record_path.write_bytes(canonical_json_bytes(record))
    record_path.chmod(0o400)
    return record_path, evidence_directory


def load_jsonl_fixture(name):
    path = ROOT / "conformance/fixtures" / name
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line
    ]


def load_job():
    path = ROOT / "conformance/fixtures/screening-jobs-valid.json"
    return json.loads(path.read_text(encoding="utf-8"))["synthetic_replicate_jobs"][0]


def base_response(record_type, variation_ids, shown_order):
    blind_labels = {
        variation_id: chr(ord("A") + shown_order.index(variation_id))
        for variation_id in variation_ids
    }
    attempts = [
        {
            "attempt_id": f"dispatch-test-reaction-{position}-attempt-1",
            "stage": "reaction",
            "position_seen": position,
            "attempt_number": 1,
            "provider_return_id": f"raw-test-r{position}-a1",
            "outcome": "accepted",
            "validation_errors": [],
        }
        for position in range(1, len(shown_order) + 1)
    ]
    attempts.append(
        {
            "attempt_id": "dispatch-test-comparison-attempt-1",
            "stage": "comparison",
            "attempt_number": 1,
            "provider_return_id": "raw-test-c-a1",
            "outcome": "accepted",
            "validation_errors": [],
        }
    )
    return {
        "study_id": "study-test",
        "response_id": f"response-{record_type}",
        "record_type": record_type,
        "method": "partial_exposure_maxdiff",
        "synthetic_replicate_id": f"replicate-{record_type}",
        "reviewer_dispatch_id": "dispatch-test",
        "persona_archetype_id": "persona-test",
        "segment_id": "segment-test",
        "profile_snapshot": {"profile_snapshot_id": "snapshot-test"},
        "context_attribute_provenance": [
            {
                "attribute": "buying_stage",
                "value": "evaluation",
                "status": "observed",
                "source_evidence": ["brief:E1"],
            }
        ],
        "worker_context_isolation": "isolated",
        "human_sample_independence": False,
        "assigned_variation_ids": list(variation_ids),
        "blind_labels": blind_labels,
        "shown_order": list(shown_order),
        "reaction_protocol": "progressive_reveal",
        "runtime_attempts": attempts,
        "validation": {
            "schema_valid": True,
            "assignment_valid": True,
            "reaction_order_valid": True,
        },
    }


def reaction(variation_id, label, position):
    return {
        "reaction_id": f"reaction-test-{position}",
        "variation_id": variation_id,
        "display_label_seen": label,
        "position_seen": position,
        "reaction_label": "immediate",
        "immediate_reaction": f"Reaction to {label}",
        "noticed_or_understood_first": f"First signal in {label}",
        "strongest_positive_signal": f"Positive signal in {label}",
        "strongest_negative_signal": f"Negative signal in {label}",
        "judgment_status": "judged",
        "source_provenance": {
            "provider_return_id": f"raw-test-r{position}-a1",
            "capture": "verbatim_provider_return",
        },
    }


def boundary_response():
    response = base_response(
        "boundary_response", ["V1", "V2"], ["V2", "V1"]
    )
    response["per_creative_reactions"] = [
        reaction("V2", "A", 1),
        reaction("V1", "B", 2),
    ]
    response["pairwise_choice"] = {
        "status": "first_preferred",
        "preferred_variation_id": "V2",
        "reason": "V2 is more credible.",
        "frozen_reaction_ids": ["reaction-test-1", "reaction-test-2"],
        "source_provenance": {
            "provider_return_id": "raw-test-c-a1",
            "capture": "verbatim_provider_return",
        },
    }
    response["usable_pairwise_observation"] = True
    return response


def finalist_response():
    variation_ids = ["V1", "V2", "V3"]
    shown_order = ["V3", "V1", "V2"]
    response = base_response("finalist_response", variation_ids, shown_order)
    reviews = []
    for position, variation_id in enumerate(shown_order, start=1):
        item = reaction(variation_id, chr(ord("A") + position - 1), position)
        item["rubric_scores"] = {key: 4 for key in RUBRIC_KEYS}
        item["feedback"] = [f"Feedback for {variation_id}"]
        item["rubric_source_provenance"] = {
            "provider_return_id": "raw-test-c-a1",
            "capture": "verbatim_provider_return",
        }
        reviews.append(item)
    response["finalist_reviews"] = reviews
    response["final_preference_ranking"] = ["V3", "V2", "V1"]
    return response


def render_workflow_prompts(job):
    synthetic_profile = {
        "persona_archetype_id": job["persona_archetype_id"],
        "profile_snapshot": job["profile_snapshot"],
    }
    job["reaction_prompts"] = [
        json.dumps(
            {
                "stage": "progressive_reaction",
                "synthetic_profiles": [synthetic_profile],
                "creative_representations": [
                    {
                        "variation_id": variation_id,
                        "display_label_seen": job["blind_labels"][variation_id],
                        "representation": {
                            "headline": f"Headline for {variation_id}",
                            "visual": f"Visual for {variation_id}",
                        },
                    }
                ],
            },
            sort_keys=True,
        )
        for variation_id in job["shown_order"]
    ]
    job["comparison_prompt"] = json.dumps(
        {
            "stage": "comparison",
            "synthetic_profiles": [synthetic_profile],
            "creative_representations": [
                {
                    "variation_id": variation_id,
                    "display_label_seen": job["blind_labels"][variation_id],
                }
                for variation_id in job["shown_order"]
            ],
        },
        sort_keys=True,
    )
    return job


def workflow_job(record_type, method="partial_exposure_maxdiff"):
    job = load_job()
    if record_type == "screening_response" and method == "complete_exposure":
        job.update(
            {
                "method": method,
                "variation_ids": ["V1", "V3", "V7"],
                "shown_order": ["V7", "V1", "V3"],
                "blind_labels": {"V7": "A", "V1": "B", "V3": "C"},
            }
        )
    elif record_type == "boundary_response":
        job.update(
            {
                "record_type": record_type,
                "variation_ids": ["V1", "V7"],
                "shown_order": ["V7", "V1"],
                "blind_labels": {"V7": "A", "V1": "B"},
            }
        )
    elif record_type == "finalist_response":
        job.update(
            {
                "record_type": record_type,
                "variation_ids": ["V1", "V3", "V7"],
                "shown_order": ["V7", "V3", "V1"],
                "blind_labels": {"V7": "A", "V3": "B", "V1": "C"},
            }
        )
    return render_workflow_prompts(job)


def run_workflow(
    *,
    record_type="screening_response",
    method="partial_exposure_maxdiff",
    exhaust_first_reaction=False,
    screening_status=None,
    malformed_non_best_first=False,
    job=None,
    force_loose_v3_jobs=False,
    omit_legacy_origin=False,
    legacy_origin_authority=None,
    capture_failure=False,
    builtin_module_mode=None,
    workflow_cwd=None,
):
    source = (ROOT / "skills/audience-ad-testing-lab/scripts/claude-large-panel-workflow.mjs").read_text(
        encoding="utf-8"
    )
    source = source.replace("export const meta =", "const meta =", 1)
    selected_job = copy.deepcopy(
        job if job is not None else workflow_job(record_type, method)
    )
    injected_test_envelope = (
        "audience_slot_id" not in selected_job
        and legacy_origin_authority is None
        and not omit_legacy_origin
    )
    if injected_test_envelope:
        selected_job.update(
            {
                "audience_slot_id": selected_job[
                    "synthetic_replicate_id"
                ],
                "grounded_profile_id": selected_job.get(
                    "grounded_profile_id",
                    "test-harness-grounded-profile",
                ),
                "profile_snapshot_sha256": "sha256:" + "0" * 64,
            }
        )
    job_json = json.dumps(selected_job)
    authenticated_envelope = (
        {
            "study_id": selected_job["study_id"],
            "method": selected_job["method"],
            "record_type": selected_job["record_type"],
            "synthetic_replicate_jobs": [selected_job],
            "audience_allocation_subset": {
                "test_harness": "already authenticated"
            },
            "audience_run_claim": "test_harness",
            "audience_dispatch": {"test_harness": "already authenticated"},
        }
        if (
            "audience_slot_id" in selected_job
            and not force_loose_v3_jobs
        )
        else None
    )
    authenticated_envelope_json = json.dumps(authenticated_envelope)
    temporary = tempfile.TemporaryDirectory()
    evidence_directory = None
    if legacy_origin_authority is not None:
        legacy_origin_value = legacy_origin_authority
    elif authenticated_envelope is None and not omit_legacy_origin:
        (
            legacy_origin_value,
            evidence_directory,
        ) = write_workflow_legacy_v2_evidence(
            Path(temporary.name),
            selected_job,
        )
        legacy_origin_value = str(legacy_origin_value)
    else:
        legacy_origin_value = None
    legacy_origin_json = json.dumps(legacy_origin_value)
    harness = f"""
import fs from "node:fs";
const source = fs.readFileSync(0, "utf8");
const job = {job_json};
const authenticatedEnvelope = {authenticated_envelope_json};
const builtinModuleMode = {json.dumps(builtin_module_mode)};
if (builtinModuleMode === "capability_unavailable") {{
  process.getBuiltinModule = undefined;
}} else if (builtinModuleMode !== null) {{
  const realGetBuiltinModule = process.getBuiltinModule.bind(process);
  process.getBuiltinModule = name => {{
    const realModule = realGetBuiltinModule(name);
    if (name === "node:fs" && builtinModuleMode === "cleanup_failure") {{
      return new Proxy(realModule, {{
        get(target, property, receiver) {{
          if (property === "rmdirSync") {{
            return () => {{
              throw new Error("injected semantic cleanup failure");
            }};
          }}
          return Reflect.get(target, property, receiver);
        }},
      }});
    }}
    if (name !== "node:child_process") {{
      return realModule;
    }}
    const semanticFailure = {{
      missing_interpreter: {{
        error: new Error("spawn python3 ENOENT"),
        status: null,
        signal: null,
        stdout: Buffer.alloc(0),
        stderr: Buffer.alloc(0),
      }},
      timeout: {{
        error: new Error("spawnSync python3 ETIMEDOUT"),
        status: null,
        signal: "SIGTERM",
        stdout: Buffer.alloc(0),
        stderr: Buffer.alloc(0),
      }},
      signal: {{
        error: undefined,
        status: null,
        signal: "SIGTERM",
        stdout: Buffer.alloc(0),
        stderr: Buffer.alloc(0),
      }},
      stderr: {{
        error: undefined,
        status: 0,
        signal: null,
        stdout: Buffer.from("{{}}\\n"),
        stderr: Buffer.from("unexpected stderr"),
      }},
      malformed_output: {{
        error: undefined,
        status: 0,
        signal: null,
        stdout: Buffer.from("{{}}\\n"),
        stderr: Buffer.alloc(0),
      }},
      nonzero: {{
        error: undefined,
        status: 1,
        signal: null,
        stdout: Buffer.alloc(0),
        stderr: Buffer.alloc(0),
      }},
      hash_mismatch: {{
        error: undefined,
        status: 0,
        signal: null,
        stdout: Buffer.from(JSON.stringify({{
          candidate_jobs_sha256: "0".repeat(64),
          producer_record_sha256: "0".repeat(64),
          schema_version: "legacy-v2-workflow-semantic-preflight-v1",
          status: "valid",
        }}, null, 2) + "\\n"),
        stderr: Buffer.alloc(0),
      }},
    }}[builtinModuleMode];
    return (
      builtinModuleMode === "cleanup_failure"
        ? realModule
        : {{ spawnSync: () => semanticFailure }}
    );
  }};
}}
const workflow = new Function(
  "args",
  "pipeline",
  "agent",
  `"use strict"; return (async () => {{\n${{source}}\n}})();`,
);
const calls = [];
let firstReactionCalls = 0;
let comparisonCalls = 0;
const screeningStatus = {json.dumps(screening_status)};
const malformedNonBestFirst = {str(malformed_non_best_first).lower()};
const legacyV2Origin = {legacy_origin_json};
const reactionReturn = position => {{
  const variationId = job.shown_order[position - 1];
  return {{
    variation_id: variationId,
    display_label_seen: job.blind_labels[variationId],
    position_seen: position,
    reaction_label: "immediate",
    immediate_reaction: `Immediate reaction ${{position}}`,
    noticed_or_understood_first: `First signal ${{position}}`,
    strongest_positive_signal: `Positive ${{position}}`,
    strongest_negative_signal: `Negative ${{position}}`,
    judgment_status: "judged",
  }};
}};
const agent = async (prompt, options) => {{
  calls.push({{ prompt, label: options.label }});
  const reactionMatch = options.label.match(/-reaction-(\\d+)/);
  if (reactionMatch) {{
    const position = Number(reactionMatch[1]);
    if (position === 1) {{
      firstReactionCalls += 1;
      if ({str(exhaust_first_reaction).lower()} || firstReactionCalls === 1) {{
        return {{
          original_payload_marker: {{
            stage: "reaction",
            position,
            nested_values: ["preserve", null, 7],
          }},
        }};
      }}
    }}
    return reactionReturn(position);
  }}
  if (options.label.includes("-comparison")) {{
    comparisonCalls += 1;
    if (job.record_type === "screening_response" && job.method === "complete_exposure") {{
      return {{
        complete_set_evaluation: {{
          status: "ranked",
          preference_ranking: [...job.shown_order],
        }},
        usable_complete_exposure_observation: true,
      }};
    }}
    if (job.record_type === "screening_response" && screeningStatus !== null) {{
      if (comparisonCalls === 1 && malformedNonBestFirst) {{
        return {{
          comparative_choice: {{
            status: screeningStatus,
            best_variation_id: "V7",
            weakest_variation_id: "V1",
          }},
          usable_maxdiff_block: false,
          original_payload_marker: {{ stage: "comparison", attempt: 1 }},
        }};
      }}
      const comparativeChoice = {{ status: screeningStatus }};
      if (screeningStatus === "unable_to_judge") {{
        comparativeChoice.best_variation_id = "";
        comparativeChoice.weakest_variation_id = "";
      }}
      return {{
        comparative_choice: comparativeChoice,
        usable_maxdiff_block: false,
      }};
    }}
    if (comparisonCalls === 1) {{
      return {{
        original_payload_marker: {{
          stage: "comparison",
          nested_values: ["preserve", null, 11],
        }},
      }};
    }}
    if (job.record_type === "boundary_response") {{
      return {{
        pairwise_choice: {{
          status: "first_preferred",
          preferred_variation_id: job.shown_order[0],
          reason: "The first creative is more credible.",
        }},
        usable_pairwise_observation: true,
      }};
    }}
    if (job.record_type === "finalist_response") {{
      return {{
        finalist_assessments: job.shown_order.map(variationId => ({{
          variation_id: variationId,
          rubric_scores: {{
            comprehension: 4,
            relevance: 4,
            credibility: 4,
            offer_appeal: 4,
            motivation: 4,
            friction: 4,
            attention_potential: 4,
            overall: 4,
          }},
          feedback: [`Feedback for ${{variationId}}`],
        }})),
        final_preference_ranking: [...job.shown_order],
      }};
    }}
    return {{
      comparative_choice: {{
        status: "best_worst",
        best_variation_id: "V7",
        weakest_variation_id: "V1",
        best_reason: "V7 is clearest.",
        weakest_reason: "V1 is least specific.",
      }},
      usable_maxdiff_block: true,
    }};
  }}
  throw new Error(`Unexpected dispatch label: ${{options.label}}`);
}};
const pipeline = async (items, fn) => Promise.all(items.map(fn));
try {{
  const result = await workflow({{
    run_id: "run-progressive-test",
    ...(authenticatedEnvelope === null
      ? {{
          synthetic_replicate_jobs: [job],
          ...({str(omit_legacy_origin).lower()}
            ? {{}}
            : {{ legacy_v2_origin_authority: legacyV2Origin }}),
        }}
      : {{ authenticated_jobs_envelope: authenticatedEnvelope }}),
    reaction_schema: {{}},
    comparison_schema: {{}},
  }}, pipeline, agent);
  process.stdout.write(JSON.stringify({{ result, calls }}));
}} catch (error) {{
  if (!{str(capture_failure).lower()}) {{
    throw error;
  }}
  process.stdout.write(JSON.stringify({{
    error: error instanceof Error ? error.message : String(error),
    calls,
  }}));
}}
"""
    try:
        completed = subprocess.run(
            ["node", "--input-type=module", "-e", harness],
            input=source,
            text=True,
            capture_output=True,
            cwd=workflow_cwd or ROOT,
            check=False,
        )
    finally:
        if evidence_directory is not None and evidence_directory.exists():
            os.chmod(evidence_directory, 0o700)
        temporary.cleanup()
    if completed.returncode != 0:
        raise AssertionError(completed.stderr)
    execution = json.loads(completed.stdout)
    if injected_test_envelope and "result" in execution:
        for response in execution["result"]["responses"]:
            for field in (
                "audience_slot_id",
                "grounded_profile_id",
                "profile_snapshot_sha256",
            ):
                response.pop(field)
    return execution


class ProgressiveWorkflowTests(unittest.TestCase):
    def test_screening_fixture_and_enriched_job_are_valid(self):
        job = load_job()
        response = load_jsonl_fixture("screening-responses-valid.jsonl")[0]

        self.assertEqual([], validate_job(job))
        self.assertEqual([], validate_response(response, job))

    def test_v3_profile_provenance_does_not_change_panelist_prompts_or_retry_behavior(
        self,
    ):
        v2_job = workflow_job("screening_response")
        v3_job = copy.deepcopy(v2_job)
        v3_job.update(
            {
                "audience_slot_id": v3_job["synthetic_replicate_id"],
                "grounded_profile_id": "grounded-profile-1",
                "profile_snapshot_sha256": "sha256:" + "1" * 64,
            }
        )

        v2_execution = run_workflow(job=v2_job)
        v3_execution = run_workflow(job=v3_job)

        self.assertEqual(v2_execution["calls"], v3_execution["calls"])
        v3_response = v3_execution["result"]["responses"][0]
        self.assertEqual(
            {
                "audience_slot_id": v3_job["audience_slot_id"],
                "grounded_profile_id": v3_job["grounded_profile_id"],
                "profile_snapshot_sha256": v3_job[
                    "profile_snapshot_sha256"
                ],
            },
            {
                key: v3_response[key]
                for key in (
                    "audience_slot_id",
                    "grounded_profile_id",
                    "profile_snapshot_sha256",
                )
            },
        )
        self.assertTrue(
            {
                "audience_slot_id",
                "grounded_profile_id",
                "profile_snapshot_sha256",
            }.isdisjoint(v2_execution["result"]["responses"][0])
        )
        v3_without_identity = copy.deepcopy(v3_execution["result"])
        for key in (
            "audience_slot_id",
            "grounded_profile_id",
            "profile_snapshot_sha256",
        ):
            v3_without_identity["responses"][0].pop(key)
        self.assertEqual(v2_execution["result"], v3_without_identity)
        self.assertEqual([], validate_job(v3_job))

    def test_workflow_rejects_an_extracted_v3_jobs_array(self) -> None:
        v3_job = workflow_job("screening_response")
        v3_job.update(
            {
                "audience_slot_id": v3_job["synthetic_replicate_id"],
                "grounded_profile_id": "grounded-profile-1",
                "profile_snapshot_sha256": "sha256:" + "1" * 64,
            }
        )

        with self.assertRaisesRegex(
            AssertionError,
            "authenticated_jobs_envelope",
        ):
            run_workflow(
                job=v3_job,
                force_loose_v3_jobs=True,
            )

    def test_workflow_rejects_a_stripped_v3_job_without_legacy_origin(
        self,
    ) -> None:
        v3_job = workflow_job("screening_response")
        v3_job.update(
            {
                "audience_slot_id": v3_job["synthetic_replicate_id"],
                "grounded_profile_id": "grounded-profile-1",
                "profile_snapshot_sha256": "sha256:" + "1" * 64,
            }
        )
        stripped = copy.deepcopy(v3_job)
        for field in (
            "audience_slot_id",
            "grounded_profile_id",
            "profile_snapshot_sha256",
        ):
            stripped.pop(field)

        with self.assertRaisesRegex(AssertionError, "origin"):
            run_workflow(job=stripped, omit_legacy_origin=True)

    def test_workflow_rejects_self_authored_marker_for_stripped_v3_job(
        self,
    ) -> None:
        v3_job = workflow_job("screening_response")
        v3_job.update(
            {
                "audience_slot_id": v3_job["synthetic_replicate_id"],
                "grounded_profile_id": "grounded-profile-1",
                "profile_snapshot_sha256": "sha256:" + "1" * 64,
            }
        )
        stripped = copy.deepcopy(v3_job)
        for field in (
            "audience_slot_id",
            "grounded_profile_id",
            "profile_snapshot_sha256",
        ):
            stripped.pop(field)
        forged = {
            "schema_version": "audience-jobs-origin-authority-v1",
            "origin": "legacy_v2",
            "producer": "prepare-panel-jobs.py",
            "producer_version": "2.0.0",
            "study_id": stripped["study_id"],
            "method": stripped["method"],
            "record_type": stripped["record_type"],
            "synthetic_replicate_ids": [
                stripped["synthetic_replicate_id"]
            ],
        }

        with self.assertRaisesRegex(
            AssertionError,
            "producer evidence|origin authority|legacy",
        ):
            run_workflow(
                job=stripped,
                legacy_origin_authority=forged,
            )

    def test_workflow_rejects_current_schema_forged_v2_record_with_invented_evidence(
        self,
    ) -> None:
        v3_job = workflow_job("screening_response")
        v3_job.update(
            {
                "audience_slot_id": v3_job["synthetic_replicate_id"],
                "grounded_profile_id": "grounded-profile-1",
                "profile_snapshot_sha256": "sha256:" + "1" * 64,
            }
        )
        stripped = copy.deepcopy(v3_job)
        for field in (
            "audience_slot_id",
            "grounded_profile_id",
            "profile_snapshot_sha256",
        ):
            stripped.pop(field)

        with self.assertRaisesRegex(
            AssertionError,
            "independent|producer evidence|origin authority|legacy",
        ):
            run_workflow(
                job=stripped,
                legacy_origin_authority=legacy_v2_record_for_job(
                    stripped
                ),
            )

    def test_workflow_rejects_resealed_fake_v2_package_evidence(
        self,
    ) -> None:
        job = workflow_job("screening_response")
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            record_path, evidence_directory = (
                write_workflow_legacy_v2_evidence(root, job)
            )
            evidence_directory.chmod(0o700)
            record_path.chmod(0o600)
            record = json.loads(
                record_path.read_text(encoding="utf-8")
            )
            evidence = record["source_dispatch_context"][
                "producer_evidence"
            ]
            fake_package = b"not-a-valid-v2-package"
            package_binding = evidence["source_package"]
            package_path = record_path.parent / package_binding["path"]
            package_path.chmod(0o600)
            package_path.write_bytes(fake_package)
            package_path.chmod(0o400)
            fake_digest = hashlib.sha256(fake_package).hexdigest()
            package_binding.update(
                {
                    "sha256": fake_digest,
                    "byte_count": len(fake_package),
                }
            )
            assignment = record["source_assignment_core"]
            assignment["audience_package"].update(
                {
                    "package_zip_sha256": fake_digest,
                    "package_zip_byte_count": len(fake_package),
                }
            )
            assignment_binding = evidence["source_assignment"]
            assignment_path = (
                record_path.parent / assignment_binding["path"]
            )
            assignment_raw = canonical_json_bytes(assignment)
            assignment_path.chmod(0o600)
            assignment_path.write_bytes(assignment_raw)
            assignment_path.chmod(0o400)
            assignment_binding.update(
                {
                    "sha256": hashlib.sha256(
                        assignment_raw
                    ).hexdigest(),
                    "byte_count": len(assignment_raw),
                }
            )
            preflight_binding = evidence[
                "source_package_validation"
            ]
            preflight_path = (
                record_path.parent / preflight_binding["path"]
            )
            preflight = json.loads(
                preflight_path.read_text(encoding="utf-8")
            )
            preflight.update(
                {
                    "package_zip_sha256": fake_digest,
                    "package_zip_byte_count": len(fake_package),
                }
            )
            preflight_raw = canonical_json_bytes(preflight)
            preflight_path.chmod(0o600)
            preflight_path.write_bytes(preflight_raw)
            preflight_path.chmod(0o400)
            preflight_binding.update(
                {
                    "sha256": hashlib.sha256(
                        preflight_raw
                    ).hexdigest(),
                    "byte_count": len(preflight_raw),
                }
            )
            identity = {
                name: evidence[name]
                for name in (
                    "source_package",
                    "source_package_validation",
                    "source_assignment",
                    "source_dispatch_context",
                    "source_manifest",
                    "produced_jobs",
                )
            }
            evidence["evidence_id"] = hashlib.sha256(
                canonical_json_bytes(identity)
            ).hexdigest()
            record_path.write_bytes(canonical_json_bytes(record))
            record_path.chmod(0o400)
            evidence_directory.chmod(0o500)

            try:
                with self.assertRaisesRegex(
                    AssertionError,
                    "package|producer evidence|origin authority",
                ):
                    run_workflow(
                        job=job,
                        legacy_origin_authority=str(
                            record_path.resolve()
                        ),
                    )
            finally:
                evidence_directory.chmod(0o700)

    def test_v2_v3_protected_outputs_are_explicitly_equal(self):
        from audience_lab.complete_exposure import aggregate_complete_exposure
        from audience_lab.finalists import aggregate_finalists
        from audience_lab.maxdiff import MaxDiffConfig, screen_shortlist
        from audience_lab.pairwise import PairwiseConfig, fit_davidson
        from conformance.test_task9_integration import (
            complete_manifest,
            complete_response,
            finalist_response as aggregate_finalist_response,
        )

        v2_job = workflow_job("screening_response")
        v3_job = copy.deepcopy(v2_job)
        v3_job.update(
            {
                "audience_slot_id": v3_job["synthetic_replicate_id"],
                "grounded_profile_id": "grounded-profile-1",
                "profile_snapshot_sha256": "sha256:" + "1" * 64,
            }
        )
        executions = {
            "v2": run_workflow(job=v2_job),
            "v3": run_workflow(job=v3_job),
        }
        jobs = {"v2": v2_job, "v3": v3_job}

        complete_records = [
            complete_response(index) for index in range(1, 10)
        ]
        maxdiff_records = json.loads(
            (
                ROOT
                / "conformance/fixtures/maxdiff-recovery.json"
            ).read_text(encoding="utf-8")
        )["observations"]
        pairwise_records = load_jsonl_fixture(
            "boundary-responses.jsonl"
        )
        manifest = complete_manifest()
        screening = {
            "study_id": manifest["study_id"],
            "method": "complete_exposure",
            "validity_status": "valid",
            "selection_status": "resolved",
            "proposed_finalist_ids": ["creative-a", "creative-b"],
        }
        approval = {
            "study_id": manifest["study_id"],
            "approved_finalist_ids": ["creative-a", "creative-b"],
            "roster_decision": {
                "status": "approved",
                "approved_at": "2026-07-22T12:00:00Z",
                "approved_by": "study owner",
                "override": False,
                "changed_after_saliency_reveal": False,
            },
        }
        finalist_records = [
            aggregate_finalist_response(
                1, ["creative-a", "creative-b"]
            ),
            aggregate_finalist_response(
                2, ["creative-b", "creative-a"]
            ),
            aggregate_finalist_response(
                3, ["creative-a", "creative-b"]
            ),
        ]

        outputs = {}
        for version in ("v2", "v3"):
            execution = executions[version]
            result = execution["result"]
            outputs[version] = {
                "response_validation": validate_response(
                    copy.deepcopy(result["responses"][0]),
                    jobs[version],
                ),
                "retry_decisions": {
                    "rejected_attempts": copy.deepcopy(
                        result["rejected_attempts"]
                    ),
                    "dispatch_audit": copy.deepcopy(
                        result["dispatch_audit"]
                    ),
                },
                "complete_exposure_scores": aggregate_complete_exposure(
                    copy.deepcopy(complete_records),
                    study_id="complete-acme-001",
                    creative_ids=[
                        "creative-a",
                        "creative-b",
                        "creative-c",
                        "creative-d",
                    ],
                    top_k=2,
                    segment_weights={"segment-1": 1.0},
                    seed=19,
                ),
                "maxdiff_results": screen_shortlist(
                    copy.deepcopy(maxdiff_records),
                    {"S1": 1.0},
                    top_k=2,
                    config=MaxDiffConfig(
                        penalty_lambda=0.1,
                        bootstrap_count=20,
                        seed=23,
                    ),
                ).as_dict(),
                "pairwise_results": fit_davidson(
                    copy.deepcopy(pairwise_records),
                    PairwiseConfig(
                        tie_parameter=0.4,
                        penalty_lambda=0.1,
                        bootstrap_count=20,
                        seed=29,
                    ),
                    candidate_ids=("V4", "V5", "V6"),
                ).as_dict(),
                "finalist_summaries": aggregate_finalists(
                    copy.deepcopy(manifest),
                    copy.deepcopy(screening),
                    copy.deepcopy(approval),
                    copy.deepcopy(finalist_records),
                ),
                "verbatim_extraction": {
                    "raw_provider_returns": copy.deepcopy(
                        result["raw_provider_returns"]
                    ),
                    "per_creative_reactions": copy.deepcopy(
                        result["responses"][0][
                            "per_creative_reactions"
                        ]
                    ),
                },
            }

        for output_name in outputs["v2"]:
            with self.subTest(output=output_name):
                self.assertEqual(
                    outputs["v2"][output_name],
                    outputs["v3"][output_name],
                )

    def test_immediate_reactions_require_progressive_reveal(self):
        errors = validate_response(
            {
                "record_type": "screening_response",
                "reaction_protocol": "reflective_reaction_caveat",
                "per_creative_reactions": [{"reaction_label": "immediate"}],
            }
        )
        self.assertIn("immediate reactions require progressive_reveal", errors)

    def test_reflective_fixture_is_valid_until_reaction_is_labeled_immediate(self):
        response = load_jsonl_fixture("screening-responses-reflective.jsonl")[0]
        self.assertEqual([], validate_response(response))

        response["per_creative_reactions"][0]["reaction_label"] = "immediate"
        errors = validate_response(response)
        self.assertIn("immediate reactions require progressive_reveal", errors)
        self.assertEqual(
            1, errors.count("immediate reactions require progressive_reveal")
        )
        self.assertIn("usable_maxdiff_block must be false", errors)

    def test_invalid_screening_reaction_protocol_makes_block_unusable(self):
        response = load_jsonl_fixture("screening-responses-reflective.jsonl")[0]
        response["reaction_protocol"] = "unsupported_protocol"

        errors = validate_response(response)

        self.assertIn("reaction_protocol is invalid", errors)
        self.assertNotIn("immediate reactions require progressive_reveal", errors)
        self.assertIn("usable_maxdiff_block must be false", errors)

    def test_unjudgeable_item_makes_maxdiff_block_unusable(self):
        response = load_jsonl_fixture("screening-responses-valid.jsonl")[0]
        response["per_creative_reactions"][0]["judgment_status"] = "unable_to_judge"
        errors = validate_response(response)
        self.assertIn("usable_maxdiff_block must be false", errors)

    def test_usable_screening_requires_frozen_reaction_ids(self):
        response = load_jsonl_fixture("screening-responses-valid.jsonl")[0]
        response["comparative_choice"].pop("frozen_reaction_ids")

        errors = validate_response(response)

        self.assertIn(
            "comparative_choice.frozen_reaction_ids must match validated reaction order",
            errors,
        )
        self.assertIn("usable_maxdiff_block must be false", errors)

    def test_usable_screening_rejects_mismatched_frozen_reaction_ids(self):
        response = load_jsonl_fixture("screening-responses-valid.jsonl")[0]
        response["comparative_choice"]["frozen_reaction_ids"] = list(
            reversed(response["comparative_choice"]["frozen_reaction_ids"])
        )

        errors = validate_response(response)

        self.assertIn(
            "comparative_choice.frozen_reaction_ids must match validated reaction order",
            errors,
        )
        self.assertIn("usable_maxdiff_block must be false", errors)

    def test_malformed_screening_reaction_order_makes_block_unusable(self):
        response = load_jsonl_fixture("screening-responses-valid.jsonl")[0]
        response["per_creative_reactions"][0]["position_seen"] = 2

        errors = validate_response(response)

        self.assertIn(
            "per_creative_reactions[0].position_seen does not match shown_order",
            errors,
        )
        self.assertIn("usable_maxdiff_block must be false", errors)

    def test_malformed_screening_reaction_provenance_makes_block_unusable(self):
        response = load_jsonl_fixture("screening-responses-valid.jsonl")[0]
        response["per_creative_reactions"][0]["source_provenance"][
            "provider_return_id"
        ] = "raw-wrong-return"

        errors = validate_response(response)

        self.assertIn(
            "per_creative_reactions[0].source_provenance.provider_return_id must identify the accepted return",
            errors,
        )
        self.assertIn("usable_maxdiff_block must be false", errors)

    def test_assignment_and_position_mismatches_are_rejected(self):
        job = load_job()
        response = load_jsonl_fixture("screening-responses-valid.jsonl")[0]
        response["assigned_variation_ids"] = ["V1", "V3", "V6", "V8"]
        response["per_creative_reactions"][0]["position_seen"] = 2

        errors = validate_response(response, job)

        self.assertIn("assigned_variation_ids must exactly match the job", errors)
        self.assertTrue(any("position_seen" in error for error in errors), errors)

    def test_provenance_and_human_independence_are_enforced(self):
        job = load_job()
        job["context_attribute_provenance"][0]["source_evidence"] = []
        self.assertTrue(
            any("source_evidence" in error for error in validate_job(job))
        )
        estimated = load_job()
        estimated["context_attribute_provenance"][0].update(
            status="estimated", source_evidence=[]
        )
        self.assertTrue(
            any("source_evidence" in error for error in validate_job(estimated))
        )
        provisional = load_job()
        provisional["context_attribute_provenance"][0].update(
            status="experimental", source_evidence=[]
        )
        self.assertFalse(
            any("source_evidence" in error for error in validate_job(provisional))
        )

        response = load_jsonl_fixture("screening-responses-valid.jsonl")[0]
        response["human_sample_independence"] = True
        response["per_creative_reactions"][0]["source_provenance"][
            "provider_return_id"
        ] = "raw-wrong-return"
        errors = validate_response(response)
        self.assertIn("human_sample_independence must be false", errors)
        self.assertTrue(
            any("must identify the accepted return" in error for error in errors),
            errors,
        )

    def test_valid_retry_history_preserves_rejected_then_accepted_attempts(self):
        response = load_jsonl_fixture("screening-responses-valid.jsonl")[0]
        accepted = response["runtime_attempts"][0]
        accepted["attempt_number"] = 2
        accepted["attempt_id"] = "dispatch-S1-replicate-0001-reaction-1-attempt-2"
        accepted["provider_return_id"] = "raw-S1-0001-r1-a2"
        response["per_creative_reactions"][0]["source_provenance"][
            "provider_return_id"
        ] = "raw-S1-0001-r1-a2"
        response["runtime_attempts"].insert(
            0,
            {
                "attempt_id": "dispatch-S1-replicate-0001-reaction-1-attempt-1",
                "stage": "reaction",
                "position_seen": 1,
                "attempt_number": 1,
                "provider_return_id": "raw-S1-0001-r1-a1",
                "outcome": "rejected",
                "validation_errors": ["reaction_id is required"],
            },
        )

        self.assertEqual([], validate_response(response, load_job()))

    def test_rejected_retry_attempt_requires_non_empty_validation_errors(self):
        for validation_errors in ([], None):
            with self.subTest(validation_errors=validation_errors):
                response = load_jsonl_fixture("screening-responses-valid.jsonl")[0]
                attempt = response["runtime_attempts"][0]
                attempt["outcome"] = "rejected"
                if validation_errors is None:
                    attempt.pop("validation_errors")
                else:
                    attempt["validation_errors"] = validation_errors

                errors = validate_response(response)

                self.assertIn(
                    "runtime_attempts[0].validation_errors must be a non-empty array for rejected attempts",
                    errors,
                )

    def test_accepted_retry_attempt_requires_empty_validation_errors(self):
        for validation_errors in (["stale validation error"], None):
            with self.subTest(validation_errors=validation_errors):
                response = load_jsonl_fixture("screening-responses-valid.jsonl")[0]
                attempt = response["runtime_attempts"][0]
                if validation_errors is None:
                    attempt.pop("validation_errors")
                else:
                    attempt["validation_errors"] = validation_errors

                errors = validate_response(response)

                self.assertIn(
                    "runtime_attempts[0].validation_errors must be an empty array for accepted attempts",
                    errors,
                )

    def test_malformed_retry_history_cannot_exceed_one_retry(self):
        response = load_jsonl_fixture("screening-responses-valid.jsonl")[0]
        response["runtime_attempts"][0]["attempt_number"] = 3

        errors = validate_response(response)

        self.assertIn("runtime attempts permit exactly one retry", errors)

    def test_boundary_response_dispatches_without_screening_fallthrough(self):
        response = boundary_response()

        self.assertEqual([], validate_response(response))

    def test_usable_boundary_requires_progressive_reactions(self):
        response = boundary_response()
        response.pop("per_creative_reactions")

        errors = validate_response(response)

        self.assertIn("per_creative_reactions must be an array", errors)
        self.assertIn("usable_pairwise_observation must be false", errors)

    def test_usable_boundary_rejects_partial_reaction_coverage(self):
        response = boundary_response()
        response["per_creative_reactions"].pop()
        response["pairwise_choice"]["frozen_reaction_ids"] = ["reaction-test-1"]

        errors = validate_response(response)

        self.assertIn(
            "per_creative_reactions must contain one record per shown creative",
            errors,
        )
        self.assertIn("per_creative_reactions creative coverage is incomplete", errors)
        self.assertIn("usable_pairwise_observation must be false", errors)

    def test_usable_boundary_requires_frozen_reaction_ids(self):
        response = boundary_response()
        response["pairwise_choice"].pop("frozen_reaction_ids")

        errors = validate_response(response)

        self.assertIn(
            "pairwise_choice.frozen_reaction_ids must match validated reaction order",
            errors,
        )
        self.assertIn("usable_pairwise_observation must be false", errors)

    def test_usable_boundary_rejects_mismatched_frozen_reaction_ids(self):
        response = boundary_response()
        response["pairwise_choice"]["frozen_reaction_ids"] = [
            "reaction-test-2",
            "reaction-test-1",
        ]

        errors = validate_response(response)

        self.assertIn(
            "pairwise_choice.frozen_reaction_ids must match validated reaction order",
            errors,
        )
        self.assertIn("usable_pairwise_observation must be false", errors)

    def test_finalist_response_dispatches_without_other_stage_fallthrough(self):
        response = finalist_response()

        self.assertEqual([], validate_response(response))

        response["final_preference_ranking"] = ["V3", "V3", "V1"]
        errors = validate_response(response)
        self.assertIn(
            "final_preference_ranking must be an exact permutation of assigned variations",
            errors,
        )

    def test_finalist_review_requires_rubric_source_provenance(self):
        response = finalist_response()
        response["finalist_reviews"][0].pop("rubric_source_provenance")

        errors = validate_response(response)

        self.assertIn(
            "finalist_reviews[0].rubric_source_provenance must be an object",
            errors,
        )

    def test_finalist_rubric_provenance_must_match_accepted_comparison_return(self):
        response = finalist_response()
        response["finalist_reviews"][0]["rubric_source_provenance"][
            "provider_return_id"
        ] = "raw-wrong-finalist-rubric-return"

        errors = validate_response(response)

        self.assertIn(
            "finalist_reviews[0].rubric_source_provenance.provider_return_id must identify the accepted return",
            errors,
        )

    def test_unsupported_record_type_does_not_fall_through(self):
        response = base_response("unknown_response", ["V1", "V2"], ["V1", "V2"])
        errors = validate_response(response)

        self.assertIn("unsupported record_type: unknown_response", errors)
        self.assertFalse(any("pairwise_choice" in error for error in errors), errors)
        self.assertFalse(any("per_creative_reactions" in error for error in errors), errors)

    def test_job_requires_exactly_one_rendered_prompt_per_exposure(self):
        job = load_job()
        job["reaction_prompts"].pop()

        errors = validate_job(job)

        self.assertIn("reaction_prompts must match shown_order length", errors)

    def test_stage_fields_are_mutually_exclusive(self):
        response = load_jsonl_fixture("screening-responses-valid.jsonl")[0]
        response["pairwise_choice"] = {"status": "tie"}

        errors = validate_response(response)

        self.assertIn("screening_response cannot contain pairwise_choice", errors)

    def test_workflow_retries_each_malformed_stage_once_and_preserves_raw_returns(self):
        execution = run_workflow()
        result = execution["result"]
        calls = execution["calls"]

        self.assertEqual("complete", result["status"])
        self.assertEqual(1, len(result["responses"]))
        self.assertEqual([], validate_response(result["responses"][0], load_job()))
        self.assertEqual(7, len(result["raw_provider_returns"]))
        self.assertEqual(2, len(result["rejected_attempts"]))
        self.assertEqual("screening_response", result["dispatch_audit"][0]["record_type"])
        self.assertEqual(
            {
                "retry_limit_per_return": 1,
                "reaction_positions": [1, 2, 3, 4],
                "comparison_required": True,
            },
            result["dispatch_audit"][0]["attempt_contract"],
        )
        self.assertNotIn("synthesis", result)
        reaction_attempts = [
            item
            for item in result["raw_provider_returns"]
            if item["stage"] == "reaction" and item["position_seen"] == 1
        ]
        comparison_attempts = [
            item
            for item in result["raw_provider_returns"]
            if item["stage"] == "comparison"
        ]
        self.assertEqual(
            {
                "original_payload_marker": {
                    "stage": "reaction",
                    "position": 1,
                    "nested_values": ["preserve", None, 7],
                }
            },
            reaction_attempts[0]["raw_return"],
        )
        self.assertNotIn("reaction_id", reaction_attempts[1]["raw_return"])
        self.assertNotIn("source_provenance", reaction_attempts[1]["raw_return"])
        self.assertEqual(
            {
                "original_payload_marker": {
                    "stage": "comparison",
                    "nested_values": ["preserve", None, 11],
                }
            },
            comparison_attempts[0]["raw_return"],
        )
        self.assertNotIn(
            "frozen_reaction_ids",
            comparison_attempts[1]["raw_return"]["comparative_choice"],
        )
        self.assertNotIn(
            "source_provenance",
            comparison_attempts[1]["raw_return"]["comparative_choice"],
        )
        reaction_calls = [call for call in calls if "-reaction-" in call["label"]]
        comparison_calls = [call for call in calls if "-comparison" in call["label"]]
        self.assertEqual(5, len(reaction_calls))
        self.assertEqual(2, len(comparison_calls))
        self.assertLess(
            max(calls.index(call) for call in reaction_calls),
            min(calls.index(call) for call in comparison_calls),
        )
        job = workflow_job("screening_response")
        expected_profile = {
            "persona_archetype_id": job["persona_archetype_id"],
            "profile_snapshot": job["profile_snapshot"],
        }
        for call in reaction_calls:
            position = int(call["label"].split("-reaction-", 1)[1].split("-", 1)[0])
            rendered_prompt = call["prompt"].split(
                "\n\nYour prior reaction return failed validation:", 1
            )[0]
            payload = json.loads(rendered_prompt)
            self.assertEqual("progressive_reaction", payload["stage"])
            self.assertEqual([expected_profile], payload["synthetic_profiles"])
            self.assertEqual(1, len(payload["synthetic_profiles"]))
            self.assertEqual(1, len(payload["creative_representations"]))
            self.assertEqual(
                job["shown_order"][position - 1],
                payload["creative_representations"][0]["variation_id"],
            )
        self.assertIn("FROZEN VALIDATED REACTIONS", comparison_calls[0]["prompt"])

    def test_non_best_screening_ids_are_rejected_once_then_python_valid(self):
        for status in ("no_meaningful_difference", "unable_to_judge"):
            with self.subTest(status=status):
                execution = run_workflow(
                    screening_status=status,
                    malformed_non_best_first=True,
                )
                result = execution["result"]
                comparison_returns = [
                    item
                    for item in result["raw_provider_returns"]
                    if item["stage"] == "comparison"
                ]

                self.assertEqual("complete", result["status"])
                self.assertEqual(2, len(comparison_returns))
                self.assertFalse(comparison_returns[0]["accepted"])
                self.assertIn(
                    f"comparative_choice.best_variation_id must be empty for {status}",
                    comparison_returns[0]["validation_errors"],
                )
                self.assertIn(
                    f"comparative_choice.weakest_variation_id must be empty for {status}",
                    comparison_returns[0]["validation_errors"],
                )
                self.assertEqual(
                    {
                        "comparative_choice": {
                            "status": status,
                            "best_variation_id": "V7",
                            "weakest_variation_id": "V1",
                        },
                        "usable_maxdiff_block": False,
                        "original_payload_marker": {
                            "stage": "comparison",
                            "attempt": 1,
                        },
                    },
                    comparison_returns[0]["raw_return"],
                )
                self.assertTrue(comparison_returns[1]["accepted"])
                response = result["responses"][0]
                self.assertEqual([], validate_response(response, load_job()))
                for field in ("best_variation_id", "weakest_variation_id"):
                    self.assertIn(
                        response["comparative_choice"].get(field),
                        (None, ""),
                    )

    def test_workflow_stops_after_one_failed_reaction_retry(self):
        execution = run_workflow(exhaust_first_reaction=True)
        result = execution["result"]
        first_reaction_calls = [
            call
            for call in execution["calls"]
            if "-reaction-1" in call["label"]
        ]

        self.assertEqual("incomplete", result["status"])
        self.assertEqual([], result["responses"])
        self.assertEqual(2, len(first_reaction_calls))
        self.assertEqual(5, len(result["raw_provider_returns"]))
        self.assertEqual(
            3,
            sum(item["accepted"] for item in result["raw_provider_returns"]),
        )
        self.assertFalse(result["dispatch_audit"][0]["accepted"])
        self.assertEqual(
            [2, 1, 1, 1], result["dispatch_audit"][0]["reaction_attempts"]
        )
        self.assertEqual(
            [1, 2],
            [
                item["attempt_number"]
                for item in result["raw_provider_returns"]
                if item["stage"] == "reaction" and item["position_seen"] == 1
            ],
        )
        self.assertFalse(
            any("-comparison" in call["label"] for call in execution["calls"])
        )

    def test_workflow_assembles_every_discriminated_response_type(self):
        for record_type in ("boundary_response", "finalist_response"):
            with self.subTest(record_type=record_type):
                execution = run_workflow(record_type=record_type)
                responses = execution["result"]["responses"]
                self.assertEqual(1, len(responses))
                self.assertEqual(record_type, responses[0]["record_type"])
                self.assertEqual(
                    [], validate_response(responses[0], workflow_job(record_type))
                )

    def test_workflow_executes_complete_exposure_without_maxdiff_fields(self):
        execution = run_workflow(method="complete_exposure")
        result = execution["result"]
        response = result["responses"][0]
        job = workflow_job("screening_response", "complete_exposure")

        self.assertEqual("complete", result["status"])
        self.assertEqual("complete_exposure", response["method"])
        self.assertEqual([], validate_job(job))
        self.assertEqual([], validate_response(response, job))
        self.assertEqual(
            job["shown_order"],
            response["complete_set_evaluation"]["preference_ranking"],
        )
        self.assertTrue(response["usable_complete_exposure_observation"])
        self.assertNotIn("comparative_choice", response)
        self.assertNotIn("usable_maxdiff_block", response)

    def test_prompt_templates_keep_reaction_comparison_and_finalist_contexts_separate(self):
        prompt = (ROOT / "skills/audience-ad-testing-lab/agents/persona-reviewer-prompt.md").read_text(
            encoding="utf-8"
        )
        reaction_section = prompt.split("## Progressive Reaction Template", 1)[1].split(
            "## Screening Comparison Template", 1
        )[0]
        comparison_section = prompt.split("## Screening Comparison Template", 1)[1].split(
            "## Boundary Pairwise Comparison Template", 1
        )[0]
        finalist_section = prompt.split("## Finalist Rubric And Ranking Template", 1)[1]

        self.assertIn("{single_creative_representation}", reaction_section)
        self.assertNotIn("{four_creative_representations}", reaction_section)
        self.assertNotIn("rubric_scores", reaction_section)
        self.assertIn("{frozen_validated_reactions}", comparison_section)
        self.assertIn("best_variation_id", comparison_section)
        self.assertIn("weakest_variation_id", comparison_section)
        self.assertIn("progressive reaction template first", finalist_section.lower())
        self.assertIn("rubric_scores", finalist_section)

    def test_collection_workflow_contains_no_post_collection_decision_stage(self):
        workflow = (ROOT / "skills/audience-ad-testing-lab/scripts/claude-large-panel-workflow.mjs").read_text(
            encoding="utf-8"
        ).lower()

        self.assertNotIn("arbiter", workflow)
        self.assertNotIn("synthesis", workflow)
        self.assertIn("raw_provider_returns", workflow)
        self.assertIn("rejected_attempts", workflow)


if __name__ == "__main__":
    unittest.main()
