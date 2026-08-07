# Input Contracts

Use this reference to decide whether a study can be planned without inventing audience, creative, method, or media evidence.

## Study Request

The deterministic planner accepts one JSON object:

```json
{
  "study_id": "screening-acme-001",
  "creative_ids": ["creative-a", "creative-b", "creative-c", "creative-d", "creative-e", "creative-f", "creative-g"],
  "creative_format": "static_image",
  "requested_shortlist_size": 3,
  "maximum_synthetic_panelists": 30,
  "target_audience": {
    "audience": "Operations leaders",
    "category": "Workflow software",
    "market": "B2B software",
    "geography": "United States",
    "buying_context": "Evaluating replacement tools",
    "exclusions": [],
    "research_mode": "public_research",
    "research_depth": "standard",
    "supplied_research_paths": []
  }
}
```

Rules:

- `creative_ids` contains 2-100 unique, nonempty IDs.
- `creative_format` is exactly one of `copy_only`, `static_image`, `carousel`, or `video_representation`.
- `requested_shortlist_size` is two to six for a 2-6 creative complete-exposure study and three to six for a 7-100 creative partial-exposure study, never greater than the creative count.
- `maximum_synthetic_panelists` is the historical field name for the user-controlled ceiling on unique synthetic-replicate/job slots used to reserve screening, boundary, and finalist work. It is not a provider/model-call ceiling and never counts people. Progressive-reveal stages increase `total_model_calls`. Retries and rejected attempts increase `total_model_calls` without creating another slot.
- The request chooses exactly one canonical audience intake route below. User-supplied `context_strata` and free-form dispatch profiles are rejected; canonical strata and profiles come only from the run-local audience resolution.

Run the planner with the real request before any large screening dispatch:

```bash
python3 scripts/plan-large-library.py study-request.json study-plan.json \
  --burden-pilot passed \
  --reported-segments <count> \
  --boundary-jobs-per-wave <count> \
  --boundary-waves-max <count> \
  --finalist-reserved <count> \
  --assignment-seed <integer>
```

## Audience Intake Routes

Choose exactly one of `target_audience`, `audience_panel`, or `provisional_audience`.

The research route is exact:

```json
{
  "target_audience": {
    "audience": "Operations leaders",
    "category": "Workflow software",
    "market": "B2B software",
    "geography": "United States",
    "buying_context": "Evaluating replacement tools",
    "exclusions": [],
    "research_mode": "public_research",
    "research_depth": "standard",
    "supplied_research_paths": []
  }
}
```

When only the audience is known, default to `public_research`; do not silently choose `provisional_no_research`. `user_provided_research`, `crm_first_party`, and `hybrid_research` require actual supplied or permissioned aggregate evidence. Research produces a validated brief, user approval, a validated panel, a deterministic package, approved registration, and run-local resolution before planning.

Saved-panel reuse is either exactly:

```json
{"audience_panel":{"source":"library","panel_id":"operations-leaders","version":"1.0.0"}}
```

or exactly:

```json
{"audience_panel":{"source":"file","package_path":"/absolute/path/audience-panel-package.zip"}}
```

Package identity is read from validated bytes. File intake accepts no caller-supplied panel identity and no unpacked directory. Both saved-panel forms resolve to `audience/snapshot` and `audience/resolution.json` before planning.

A Tier 3 v3 package additionally contains the canonical
`authorized-audience-runtime-authority.json`: the actual approved aggregate
handoff and selected structural observation batches. Their hashes, authorized
route, unit/denominator, and exact cohort must match the brief, panel, and
population frame. Resolution writes an independent
`audience/resolution-authority.json` beside the envelope; historical reloads
bind the original clock and input hashes to that authority rather than trusting
`resolution.json.resolved_at` as its own expectation.

The explicit lower-confidence exception is exact:

```json
{
  "provisional_audience": {
    "scope": {
      "audience": "Operations leaders",
      "market": "B2B software",
      "geography": "United States",
      "category": "Workflow software",
      "buying_context": "Evaluating replacement tools",
      "exclusions": []
    },
    "user_defined_segments": [
      {"segment_id":"operations-leaders","name":"Operations leaders","description":"User-defined planning segment"}
    ],
    "accepted_by": "study owner",
    "accepted_at": "2026-07-22T12:00:00Z",
    "expires_at": "2026-08-21T12:00:00Z"
  }
}
```

`accepted_by`, `accepted_at`, and `expires_at` are runtime-populated control
fields, not a second user approval. The user's choice to proceed with a
no-research creative test is the route decision. The runtime derives
`accepted_by` from the run owner, sets `accepted_at` to the route-selection
timestamp, and calculates `expires_at` automatically at no more than 30 days
after that timestamp. Unsupported audience and profile fields are written as
`unknown`, not requested as placeholder approvals or invented.

The deterministic runtime creates a labeled package for the immediate run without registration. It uses no research sources, is not research-backed,
and cannot be resolved later through `audience_panel`. When no creatives are
available, retain only a draft audience scope and do not materialize the
provisional package.

## Required Decision Context

Lock this brief before planning:

```yaml
ad_test_context:
  primary_decision: ""
  campaign_goal: ""
  funnel_stage: ""
  success_metric: ""
  audience_summary: ""
  market_and_buying_context: ""
  offer_or_product: ""
  brand_constraints: []
  evaluation_mode: ad_message | platform_agnostic | single_platform | cross_platform
  platform_contexts: []
  assumptions: []
```

Platform names and placements are open-ended. Score platform fit only for `single_platform` or `cross_platform`; otherwise retain platform information as context.

## Method Selection

The only supported method IDs are:

- `complete_exposure`: executable complete-set route for 2-6 creatives. Every screening job contains the whole creative set.
- `partial_exposure_maxdiff`: route for 7-100 creatives after the burden pilot passes. Each screening assignment contains exactly four creatives.

If the burden pilot is failed or unrun, the deterministic route is `split_required`. Do not relabel that state as either supported method.

Complete exposure uses a versioned profile-aware capacity plan for new v3 runs, progressive reveal, a separate `complete_set_evaluation`, and deterministic whole-record bootstrap within grounded profile. Frozen v2 runs retain nine counterbalanced complete-set jobs per reported segment. It never uses four-item MaxDiff, subset, boundary-candidate, or Davidson fields. `partial_exposure_maxdiff` uses four-item assignments and may use a separately planned boundary stage. One study never mixes the first-round estimands.

The plan records `reported_segment_ids` and carries each supplied context stratum into `assignment.context_strata`, `context_stratum_allocations`, and the frozen jobs. `scripts/prepare-panel-jobs.py` must resolve a profile for the exact planned `(segment_id, context_stratum_id)` pair; a segment-only or differently named context profile cannot silently replace it. Aggregation preserves accepted record counts by both segment and context stratum, and the dashboard reports represented context strata separately from synthetic replicates.

For partial exposure, first-round aggregation requires the frozen screening jobs and validates each accepted record against its exact job. If one or more planned jobs have no accepted composite response, provide `dispatch-audit.jsonl`; each missing job must be present in the audit with an exact terminal reaction or comparison retry-exhaustion sequence bound to the manifest retry policy. The audit cannot admit an unplanned or altered response.

## Audience And Context Provenance

New or materially updated audience models require an approved `audience-research-brief-v2` and a validated `saved-audience-panel-v2`. Each grounded context dimension uses:

```yaml
context_dimension:
  name: buying_stage
  value: active_evaluation
  status: observed | estimated | experimental
  source_evidence:
    - evidence-id
  finding_ids:
    - finding-id
```

- `observed`: directly supported by supplied or cited evidence.
- `estimated`: inferred from evidence with a stated uncertainty boundary.
- `experimental`: deliberately varied to test sensitivity, not claimed as observed audience prevalence.

Do not combine individually plausible attributes into an unsupported joint profile. Record the joint-combination basis or omit the combination.

A `context_stratum` contains `context_stratum_id`, `segment_id`, positive `planned_weight`, `weighting_rule`, and one or more provenance-bound dimensions. Context weights plan assignments; they do not prove market prevalence. Only explicit validated `grounded_context_profiles` combinations may be instantiated; never form an implicit archetype-by-stratum cross-product.

## Creative Roster

Preserve exact creative inputs in `creative-roster.json`:

```yaml
study_id: ""
creatives:
  - variation_id: creative-a
    display_name: Proof before promise
    format: static_image
    headline: ""
    body: ""
    cta: ""
    visual_description: ""
    input_fidelity: supplied_asset | inspected_representation | supplied_description | copy_only
    media:
      - representation_id: creative-a-static-01
        kind: image | carousel_card | thumbnail | keyframe | video_frame
        timestamp: ""
        path: media/creative-a.png
        mime_type: image/png
        content_hash: sha256:<hex>
        label: ""
        alt: ""
```

For every media entry:

- `representation_id` is stable and unique within the study.
- `content_hash` is the SHA-256 hash of the exact tested bytes, prefixed with `sha256:`.
- The file resolves inside the run directory and has a renderable image MIME type.
- Carousel cards and supplied video frames each receive their own representation.
- Copy and media shown to workers match the locked roster. Do not substitute a later crop or export under the same hash.

Format rules:

- `copy_only` has no inspectable media entries.
- `static_image` represents supplied static imagery.
- `carousel` represents each supplied card separately and preserves card order.
- `video_representation` records exactly which thumbnail, keyframe, or video-frame representations were inspected. Transcript, captions, storyboard, timing, or audio notes may accompany them but do not become image representations.

If the study claims an imagery format but its decision-critical imagery is unavailable, stop and request the missing representation. Do not silently change the format or fabricate a visual description as if it were inspected media.

## Automatic Heatmap Inputs

Automatically generate or import one attention heatmap for every inspectable media representation after roster approval. The run plan must declare the intended target for each representation before any overlay is revealed:

```yaml
attention_target_plan:
  - representation_id: creative-a-static-01
    predeclared_target: "Offer and CTA"
    target_declared_at: "2026-07-22T14:00:00Z"
    provider_plan: sum | imported_heatmap | another_computational_provider
    method_plan: ""
```

`static_image`, `carousel`, and `video_representation` require this plan. `copy_only` is the sole normal omission route and later renders **No imagery was tested.**

A handwritten or model-authored visual observation is a heuristic outside evidence scoring. It does not satisfy the computational/imported heatmap requirement.

For imagery, a missing, incomplete, untimely, or unhashed heatmap entry is a hard stop before dashboard rendering.

## Runtime And Model Lock

The manifest records, at minimum:

```yaml
runtime:
  orchestration_mode: context_isolated_workers | shared_context_fallback
  provider: ""
  model_revision: ""
  decoding_parameters: {}
  prompt_contract_version: ""
  rendered_prompt_hashes: []
  code_commit: ""
  worker_context_isolation: isolated | shared_context_fallback
  retry_limit_per_return: 1
model:
  maxdiff_version: joint-maxdiff-v1
  penalty_type: l2
  penalty_lambda: 0.1
  optimizer_tolerance: 0.000001
  bootstrap_count: 2000
  clear_finalist_threshold: 0.90
  clear_non_finalist_threshold: 0.10
  pairwise_model: davidson
  pairwise_tie_parameter: 0.2
  pairwise_penalty_lambda: 0.1
  pairwise_optimizer_tolerance: 0.000001
```

Every response states `human_sample_independence: false`. Worker isolation is a review-integrity control, not a human sampling property.

`panelist-responses.jsonl`, `raw-provider-returns.jsonl`, `rejected-attempts.jsonl`, and `dispatch-audit.jsonl` are the canonical lineage filenames. `scripts/materialize-run-lineage.py` writes them from workflow arrays and binds their paths, SHA-256 hashes, and record counts under manifest `outputs`; one shared validator is used by materialization, dashboard rendering, and standalone validation before the files appear in Downloads.

## Capacity Lock

The planner freezes:

```yaml
synthetic_replicate_capacity:
  screening_planned: 0
  boundary_jobs_per_wave: 0
  boundary_waves_max: 0
  boundary_reserved: 0
  finalist_reserved: 0
  required_total: 0
  ceiling: 0
  ceiling_satisfied: false
```

Every capacity field above counts unique synthetic-replicate/job slots, not provider/model-call attempts. `boundary_reserved = boundary_jobs_per_wave * boundary_waves_max`. Screening cannot consume boundary or finalist slots. Boundary work cannot consume `finalist_reserved`. Retries and rejected attempts increase `total_model_calls`; they do not consume another unique job slot. If the slot ceiling is not satisfied, stop before assignment or dispatch.

## Performance Evidence

Row-level campaign, CRM, account, or customer data is outside this skill's boundary. The user or outer orchestration layer must prepare it separately with Audience Data Lab and supply:

```text
audience-performance-evidence-v1
```

The handoff must be approved, must authorize `ad_test_calibration`, and must contain only privacy-reviewed aggregate results. Validate it before use:

```bash
python3 scripts/validate-performance-evidence.py <handoff.json>
```

Supply run, variant, segment, channel, objective, and time-window mapping notes separately because an approved aggregate evidence package may be relevant to more than one run. Weakly mapped or blended evidence is not ground truth. Never use future performance evidence to rewrite the original synthetic response records.
