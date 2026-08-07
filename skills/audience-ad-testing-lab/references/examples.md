# Fictional Examples

Every worked example uses fictional Acme data. The values illustrate contract shape only; they are not evidence from a real client, audience, or campaign.

## Reusable Audience Panel Lifecycle

**Ad Testing Lab** is the display name. Keep the compatibility identifiers unchanged in paths and integrations: skill slug `audience-ad-testing-lab`, Python package `audience_lab`, and the existing schema and manifest IDs.

The commands below assume the current directory is the installed skill directory containing `SKILL.md`. They emit one JSON object to stdout so automation can record the exact package and library result.

### 1. Build and review

Build only after the research brief is approved and the panel validates against that exact brief:

```bash
python3 scripts/build-audience-package.py \
  --brief /absolute/path/to/persona-research-brief.json \
  --panel /absolute/path/to/saved-audience-panel.json \
  --output-dir /absolute/path/to/acme-audience-package
```

Review `audience-research-report.html` and `research-sources.csv` in the output directory. The reusable file is `audience-panel-package.zip`. The JSON and manifest files are the technical record; they are not the marketer-facing deliverable.

### 2. Register, list, and inspect

Registration writes to the local immutable library. Do it only after the user approves reuse:

```bash
python3 scripts/manage-audience-library.py register \
  /absolute/path/to/acme-audience-package/audience-panel-package.zip
python3 scripts/manage-audience-library.py list
python3 scripts/manage-audience-library.py show acme-operations-transformation 1.0.0
```

The default library is `~/.audience-ad-testing-lab/library`. For tests or a temporary review, isolate it explicitly without touching the real library:

```bash
AUDIENCE_LAB_LIBRARY_DIR=/absolute/path/to/temporary-library \
  python3 scripts/manage-audience-library.py register \
  /absolute/path/to/acme-audience-package/audience-panel-package.zip
```

The configured library path must be absolute. Do not point it at a symlink or a shared checkout.

### 3. Resolve for a study

Create an intake file that names the exact immutable version:

```json
{
  "audience_panel": {
    "source": "library",
    "panel_id": "acme-operations-transformation",
    "version": "1.0.0"
  }
}
```

Create a study-scope JSON object containing `audience`, `market`, `geography`, `category`, `buying_context`, and `exclusions`, then resolve it into the run:

```bash
python3 scripts/manage-audience-library.py resolve \
  /absolute/path/to/audience-intake.json \
  /absolute/path/to/study-scope.json \
  /absolute/path/to/study-run
```

A `ready` result writes a hash-bound snapshot under `study-run/audience/`. Planning consumes that resolution. It does not rebuild research.

### 4. Refresh without overwriting history

Resolution returns exit code `5` with `needs_refresh` or `incompatible` when scope or freshness rules block reuse. An explicit known trigger can be checked with `--refresh-trigger`:

```bash
python3 scripts/manage-audience-library.py resolve \
  /absolute/path/to/audience-intake.json \
  /absolute/path/to/study-scope.json \
  /absolute/path/to/study-run \
  --refresh-trigger "New first-party evidence"
```

Do not edit or replace the registered ZIP. Refresh the research brief, obtain approval again, build a matching panel with a new semantic version, package it, and register the new version. Provisional no-research packages are immediate-run files only; they cannot be registered or reused.

### 5. Export and share safely

For a marketer or reviewer, export these three files together:

- `audience-research-report.html` — readable methodology, segments, mindsets, evidence, gaps, and limits;
- `research-sources.csv` — spreadsheet-ready evidence register;
- `audience-panel-package.zip` — validated reusable package for an authorized Ad Testing Lab installation.

Before sharing, confirm the recipient is authorized for the underlying research, the CSV contains no raw names, email addresses, account identifiers, or row-level CRM data, and the package is research-backed rather than provisional. Send the package ZIP as a file; do not send the local library directory or `index.json`. Preserve the ZIP unchanged so its recorded SHA-256 hash remains verifiable.

## Complete-Exposure Example

```markdown
**Existing Run Record**

**Study:** `acme-copy-001`
**Decision:** Choose one of four message routes for a controlled live test.
**Creative format:** `copy_only`
**Method:** `complete_exposure`
**Audience basis:** Approved Acme operations-leader brief with four persona archetypes and eight grounded context profiles.
**Exposure record:** The profile-aware planner created a frozen core plus balanced reserve blocks; every synthetic execution saw all four ads through progressive reveal. The run report lists grounded profiles, planned executions, usable floors, accepted records, model calls, and human respondents separately.
**Collection record:** Fresh context per synthetic replicate where available; `human_sample_independence: false` in every response.
**Finalist deep review:** Whole-number 1-5 rubric plus exact ranking of the approved set.
**Heatmap:** No imagery was tested.
**Limits:** Complete-set utilities and stability are conditional on this recorded synthetic protocol, not customer or market preference.
```

This method never uses four-item MaxDiff blocks or a Davidson boundary stage. The method-aware response validator and deterministic complete-set aggregator execute the plan directly.

## Large Static-Image Study Request

```json
{
  "study_id": "acme-static-012",
  "creative_ids": [
    "creative-a",
    "creative-b",
    "creative-c",
    "creative-d",
    "creative-e",
    "creative-f",
    "creative-g",
    "creative-h",
    "creative-i",
    "creative-j",
    "creative-k",
    "creative-l"
  ],
  "creative_format": "static_image",
  "requested_shortlist_size": 5,
  "maximum_synthetic_panelists": 96,
  "context_strata": [
    {
      "context_stratum_id": "active-evaluation",
      "segment_id": "operations-leader",
      "planned_weight": 1.0,
      "weighting_rule": "approved target mix",
      "dimensions": [
        {
          "name": "buying_stage",
          "value": "active_evaluation",
          "status": "observed",
          "source_evidence": ["acme-brief-evidence-2"]
        }
      ]
    }
  ]
}
```

Plan it before fan-out:

```bash
python3 scripts/plan-large-library.py acme-study-request.json acme-study-plan.json \
  --burden-pilot passed \
  --reported-segments 1 \
  --boundary-jobs-per-wave 6 \
  --boundary-waves-max 2 \
  --finalist-reserved 20 \
  --assignment-seed 17
```

The resulting method is `partial_exposure_maxdiff`. Screening, boundary, and finalist reserves must fit under 96 unique synthetic-replicate/job slots before dispatch, not 96 model calls. Progressive-reveal stages, retries, and rejected attempts increase `total_model_calls` without creating another slot.

## Media Representation

```yaml
variation_id: creative-a
display_name: Proof before promise
format: static_image
headline: "See the delay before it costs another week"
body: "Map the stalled handoff before the next planning cycle."
cta: "Request a workflow review"
input_fidelity: supplied_asset
media:
  - representation_id: creative-a-static-01
    kind: image
    path: media/creative-a.png
    mime_type: image/png
    content_hash: sha256:<original-hex>
    label: Supplied static creative
    alt: Acme workflow ad with one delayed handoff highlighted
```

Declare its target during planning:

```yaml
representation_id: creative-a-static-01
predeclared_target: "Proof cue and CTA"
target_declared_at: "2026-07-22T15:00:00Z"
provider_plan: SUM
method_plan: computational saliency
```

## Screening Response Shape

```json
{
  "study_id": "acme-static-012",
  "response_id": "screening-response-0001",
  "record_type": "screening_response",
  "method": "partial_exposure_maxdiff",
  "synthetic_replicate_id": "operations-leader-replicate-0001",
  "reviewer_dispatch_id": "dispatch-0001",
  "persona_archetype_id": "evidence-first-operator",
  "segment_id": "operations-leader",
  "profile_snapshot": {
    "profile_snapshot_id": "acme-operations-leader-v1",
    "context_stratum_id": "active-evaluation"
  },
  "context_attribute_provenance": [
    {
      "attribute": "buying_stage",
      "value": "active_evaluation",
      "status": "observed",
      "source_evidence": ["acme-brief-evidence-2"]
    }
  ],
  "worker_context_isolation": "isolated",
  "human_sample_independence": false,
  "assigned_variation_ids": ["creative-a", "creative-d", "creative-g", "creative-j"],
  "blind_labels": {
    "creative-a": "B",
    "creative-d": "D",
    "creative-g": "A",
    "creative-j": "C"
  },
  "shown_order": ["creative-g", "creative-a", "creative-j", "creative-d"],
  "reaction_protocol": "progressive_reveal",
  "runtime_attempts": [
    {"attempt_id": "dispatch-0001-reaction-1-attempt-1", "stage": "reaction", "position_seen": 1, "attempt_number": 1, "provider_return_id": "raw-dispatch-0001-r1", "outcome": "accepted", "validation_errors": []},
    {"attempt_id": "dispatch-0001-reaction-2-attempt-1", "stage": "reaction", "position_seen": 2, "attempt_number": 1, "provider_return_id": "raw-dispatch-0001-r2", "outcome": "accepted", "validation_errors": []},
    {"attempt_id": "dispatch-0001-reaction-3-attempt-1", "stage": "reaction", "position_seen": 3, "attempt_number": 1, "provider_return_id": "raw-dispatch-0001-r3", "outcome": "accepted", "validation_errors": []},
    {"attempt_id": "dispatch-0001-reaction-4-attempt-1", "stage": "reaction", "position_seen": 4, "attempt_number": 1, "provider_return_id": "raw-dispatch-0001-r4", "outcome": "accepted", "validation_errors": []},
    {"attempt_id": "dispatch-0001-comparison-attempt-1", "stage": "comparison", "attempt_number": 1, "provider_return_id": "raw-dispatch-0001-comparison", "outcome": "accepted", "validation_errors": []}
  ],
  "validation": {
    "schema_valid": true,
    "assignment_valid": true,
    "reaction_order_valid": true
  },
  "per_creative_reactions": [
    {
      "reaction_id": "reaction-0001-1",
      "variation_id": "creative-g",
      "display_label_seen": "A",
      "position_seen": 1,
      "reaction_label": "immediate",
      "immediate_reaction": "The operating problem is immediately clear.",
      "noticed_or_understood_first": "The delayed handoff.",
      "strongest_positive_signal": "The proof cue is concrete.",
      "strongest_negative_signal": "The body copy is dense.",
      "judgment_status": "judged",
      "source_provenance": {"provider_return_id": "raw-dispatch-0001-r1", "capture": "verbatim_provider_return"}
    },
    {
      "reaction_id": "reaction-0001-2",
      "variation_id": "creative-a",
      "display_label_seen": "B",
      "position_seen": 2,
      "reaction_label": "immediate",
      "immediate_reaction": "The promise is specific enough to inspect.",
      "noticed_or_understood_first": "The workflow-delay claim.",
      "strongest_positive_signal": "The CTA matches the problem.",
      "strongest_negative_signal": "The brand cue is quiet.",
      "judgment_status": "judged",
      "source_provenance": {"provider_return_id": "raw-dispatch-0001-r2", "capture": "verbatim_provider_return"}
    },
    {
      "reaction_id": "reaction-0001-3",
      "variation_id": "creative-j",
      "display_label_seen": "C",
      "position_seen": 3,
      "reaction_label": "immediate",
      "immediate_reaction": "The category is clear but the outcome is vague.",
      "noticed_or_understood_first": "The product category.",
      "strongest_positive_signal": "The layout scans quickly.",
      "strongest_negative_signal": "The benefit lacks proof.",
      "judgment_status": "judged",
      "source_provenance": {"provider_return_id": "raw-dispatch-0001-r3", "capture": "verbatim_provider_return"}
    },
    {
      "reaction_id": "reaction-0001-4",
      "variation_id": "creative-d",
      "display_label_seen": "D",
      "position_seen": 4,
      "reaction_label": "immediate",
      "immediate_reaction": "The message feels generic.",
      "noticed_or_understood_first": "The stock workflow visual.",
      "strongest_positive_signal": "The brand is recognizable.",
      "strongest_negative_signal": "The claim could apply to any vendor.",
      "judgment_status": "judged",
      "source_provenance": {"provider_return_id": "raw-dispatch-0001-r4", "capture": "verbatim_provider_return"}
    }
  ],
  "comparative_choice": {
    "status": "best_worst",
    "best_variation_id": "creative-g",
    "weakest_variation_id": "creative-d",
    "best_reason": "Creative G makes the operating problem and proof easiest to connect.",
    "weakest_reason": "Creative D is the least specific.",
    "frozen_reaction_ids": ["reaction-0001-1", "reaction-0001-2", "reaction-0001-3", "reaction-0001-4"],
    "source_provenance": {"provider_return_id": "raw-dispatch-0001-comparison", "capture": "verbatim_provider_return"}
  },
  "usable_maxdiff_block": true
}
```

Validate accepted records against jobs:

```bash
python3 scripts/validate-panel-run.py acme-jobs.json \
  --legacy-v2-origin-authority acme-legacy-v2-origin-authority.json \
  --responses acme-screening-responses.jsonl --expected-count 27
```

The collection result retains every dispatched slot, provider call, accepted response, and rejected validation attempt. Run `scripts/materialize-run-lineage.py` to write `panelist-responses.jsonl`, `raw-provider-returns.jsonl`, `rejected-attempts.jsonl`, and `dispatch-audit.jsonl`; bind their paths, hashes, and record counts under manifest `outputs`; and expose them as validated dashboard downloads.

## Deterministic Screening

```bash
python3 scripts/aggregate-screening.py screening \
  --manifest study-manifest.json \
  --jobs acme-jobs.json \
  --responses acme-screening-responses.jsonl \
  --dispatch-audit dispatch-audit.jsonl \
  --recovery-config references/screening-recovery-config.json \
  --output screening-model-results.json
```

The frozen jobs file prevents an internally consistent but unplanned response from entering the model. Include the dispatch audit whenever a planned job exhausted its allowed reaction or comparison retries without producing an accepted composite response.

Example interpretation:

```yaml
estimand: centered_protocol_relative_log_utility
stability_diagnostic: conditional_within_run_top_k_inclusion_frequency
validity_status: exploratory
validity_reasons:
  - recovery_configuration_exploratory_only
classifications:
  creative-a: unresolved
  creative-b: unresolved
```

Even if raw inclusion frequencies look separated, an `exploratory_only` recovery configuration cannot publish clear groups or a `valid` state.

## Frozen Boundary Handoff

When a calibrated run yields boundary candidates, freeze the clear groups and pair plan. Dispatch only wave 1, then run:

```bash
python3 scripts/aggregate-screening.py boundary \
  --manifest study-manifest.json \
  --screening-results screening-model-results.json \
  --responses boundary-responses.jsonl \
  --output boundary-results.json
```

If the result is `unresolved`, either dispatch exactly `decision_audit.next_wave_job_ids` or stop when no authorized work remains. Never let an arbiter pick a boundary ad from prose.

## Awaiting-Approval Finalist State

```json
{
  "study_id": "acme-static-012",
  "approved_finalist_ids": ["creative-a", "creative-b", "creative-c", "creative-d", "creative-e"],
  "roster_decision": {
    "status": "awaiting_approval",
    "override": false,
    "changed_after_saliency_reveal": false
  },
  "accepted_response_records": null,
  "accepted_unique_replicates": null,
  "unique_job_slots_consumed": null,
  "total_model_calls": null,
  "first_choice_counts": {},
  "conditional_first_choice_share": {},
  "rubric_summary": {},
  "testing_map": []
}
```

The dashboard calls these pending finalists. It does not say they moved forward and exposes no finalist metrics.

## Approved Finalist Result

```yaml
roster_decision:
  status: approved
  approved_at: "2026-07-22T16:00:00Z"
  approved_by: study_owner
  override: false
  changed_after_saliency_reveal: false
accepted_response_records: 12
accepted_unique_replicates: 12
unique_job_slots_consumed: 12
total_model_calls: 72
first_choice_counts:
  creative-a: 5
  creative-b: 3
  creative-c: 2
  creative-d: 1
  creative-e: 1
conditional_first_choice_share:
  creative-a: 0.4166666667
  creative-b: 0.25
  creative-c: 0.1666666667
  creative-d: 0.0833333333
  creative-e: 0.0833333333
```

Describe `0.4166666667` only as **First-choice share in this finalist round: 5 of 12 accepted finalist AI reviews**. It is conditional on this five-ad finalist set.

## Automatic Heatmap Index

```yaml
study_id: acme-static-012
status: available
provider: SUM
method: computational saliency
revealed_at: "2026-07-22T16:05:00Z"
entries:
  - variation_id: creative-a
    representation_id: creative-a-static-01
    content_hash: sha256:<original-hex>
    original_path: media/creative-a.png
    overlay_path: media/creative-a-overlay.png
    overlay_content_hash: sha256:<overlay-hex>
    predeclared_target: "Proof cue and CTA"
    target_declared_at: "2026-07-22T15:00:00Z"
    categorical_alignment: partially_aligned
    provider: SUM
    limitations:
      - "Predicted visual prominence is not eye tracking or behavioral evidence."
```

The complete file contains one entry for every tested media representation. Missing even one entry blocks rendering.

## Marketer Summary

```markdown
## What this test says

Twelve ads entered the first round. Five are pending or approved according to the recorded roster state. Creative A produced the strongest conditional first-round signal, while Creative B remained close enough to matter for the live test design.

The first-round read is model-conditional. The dashboard reports total model calls, accepted response records, accepted unique replicates by stage, audience archetypes, grounded context profiles, and all run-specific limits separately.
```

## Dashboard Commands

```bash
python3 scripts/render-dashboard.py --run-dir /absolute/path/to/acme-run --output /absolute/path/to/acme-dashboard.html
python3 scripts/validate-dashboard.py /absolute/path/to/acme-dashboard.html
```

The dashboard navigation is: Overview, Ads tested, All ad results, Top ads, Feedback, Attention heatmap (imagery only), AI audience responses, Methodology, Downloads.
