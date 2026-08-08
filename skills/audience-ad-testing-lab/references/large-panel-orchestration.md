# Large-Library Runtime Orchestration

Use this reference for method-aware collection, bounded worker waves, retries/resume, or predeclared partial-exposure boundary collection.

## Required Stage Order

1. Freeze audience, context provenance, creative roster, hashes, method inputs, and the unique synthetic-replicate/job-slot ceiling.
2. Run `python3 scripts/plan-large-library.py`.
3. Obtain run-plan approval.
4. Enrich and validate `synthetic_replicate_jobs`.
5. Collect and validate `screening_response` records with progressive reveal.
6. Run `python3 scripts/aggregate-screening.py screening`.
7. For partial exposure only, freeze clear groups, boundary candidates, and the authorized pairwise wave plan.
8. For partial exposure only, collect the currently authorized `boundary_response` wave.
9. Run `python3 scripts/aggregate-screening.py boundary` after each complete authorized wave; complete exposure skips directly to the proposal or an unresolved cutoff-tie state.
10. Present the proposed roster as `awaiting_approval`.
11. After approval, collect fresh `finalist_response` records on the complete approved set.
12. Generate or import every required per-representation attention heatmap.
13. Run arbiter synthesis from deterministic outputs.
14. Render and validate the dashboard.

The planner must run before screening fan-out. The method-aware aggregator must run before arbiter synthesis. Do not collapse, reorder, or let a prompt infer these stage transitions.

## Capability Detection

Detect exposed runtime tools; do not infer capability from product name.

```yaml
orchestration:
  mode: claude_dynamic_workflow | independent_subagents | isolated_sequential_agents | shared_context_only
  requested_replicates: 0
  planned_replicates: 0
  observed_max_concurrency: 0
  planned_waves: 0
  worker_unit: one_context_isolated_synthetic_replicate_per_worker
  workflow_or_agent_tool: ""
  retry_limit_per_return: 1
  resume_support: ""
  fallback_reason: ""
```

Use one context-isolated synthetic replicate per worker where available. Isolation means a worker sees one frozen profile and no other replicate or aggregate results. It does not create human-sample independence; every response records `human_sample_independence: false`.

`shared_context_only` cannot support the same Review integrity state. Stop at a dispatch package or run a visibly limited simulation; do not make a large-sample claim.

## Approval Gate

Before paid or high-volume fan-out, show:

- creative count, canonical format, method, shortlist size, and burden-pilot result;
- research brief, segment weights, context strata, and provenance gaps;
- planned screening assignments and their balance/connectivity diagnostics;
- `screening_planned`, `boundary_reserved`, and `finalist_reserved` under the unique-job-slot ceiling;
- runtime mode, observed concurrency, wave count, retry/resume behavior, and projected usage range;
- blind labels, shown-order rule, and progressive-reveal protocol;
- intended attention target for every media representation;
- incomplete-run and refusal behavior.

Approval of research is not approval to dispatch. Approval of the run plan authorizes only the frozen plan.

## Planning And Assignment

Run:

```bash
python3 scripts/plan-large-library.py study-request.json study-plan.json \
  --burden-pilot passed \
  --reported-segments <count> \
  --boundary-jobs-per-wave <count> \
  --boundary-waves-max <count> \
  --finalist-reserved <count> \
  --assignment-seed <integer> \
  --audience-resolution <run-directory>/audience/resolution.json
```

Partial exposure is the route for 7-100 creatives when the burden pilot passes. Large-set assignments use four-item blocks and profile-conditioned coverage diagnostics. Complete exposure is the route for 2-6 creatives; new v3 runs use a dynamic profile-aware core plus balanced reserves, while frozen v2 runs retain nine counterbalanced full-set jobs per reported segment. Never dispatch four-item jobs under the complete-exposure method.

When `context_strata` are present, they define the canonical audience identity. The planner rejects a `--reported-segments` count that differs from the number of distinct supplied `segment_id` values, preserves the supplied segment and `context_stratum_id` values on every planned job, and balances complete-exposure jobs across those real IDs. It creates placeholder `segment-N` identities only when the request contains no context strata. The dispatch adapter must resolve the exact planned segment-plus-context-stratum profile; a profile from another stratum is not interchangeable.

For v3 complete exposure, calculate capacity from grounded profiles, frozen weights, creative count, usable floors, balanced failure reserve, finalist reserve, and the authorized ceiling. Do not use a universal executions-per-segment or executions-per-profile multiplier.

Capacity counts unique synthetic-replicate/job slots, not provider/model-call attempts, and is binding:

```text
boundary_reserved = boundary_jobs_per_wave * boundary_waves_max
required_total = screening_planned + boundary_reserved + finalist_reserved
```

If `required_total` exceeds the slot ceiling, stop. Do not reduce assignments or consume a later-stage reserve without a new plan and approval. Progressive-reveal stages, retries, and rejected attempts increase `total_model_calls` but do not create another job slot.

For v3, planning also freezes one exact profile assignment for every screening,
boundary-reserve, and finalist-reserve slot. Each roster binds the resolution
envelope, stable seed, allocation basis, profile snapshot hash, reported
segment, structural group, fidelity diagnostics, and claim effect; their
combined hash binds the plan. Tier 2/3 screening matches only within the
reported segment and preserves the structural frame. Tier 1 uses
`directional_profile_allocation` and carries no frame-fidelity status.
Finalists remain unweighted in analysis even though their profile identities
are frozen before outcomes.

## Enriched Dispatch Contract

The planner produces an assignment core. Before collection, enrich each job with the exact runtime fields required by `scripts/claude-large-panel-workflow.mjs` and `scripts/validate-panel-run.py`:

```json
{
  "run_id": "run-001",
  "synthetic_replicate_jobs": [
    {
      "study_id": "run-001",
      "response_id": "screening-response-0001",
      "record_type": "screening_response",
      "synthetic_replicate_id": "marketing-leader-replicate-0001",
      "dispatch_id": "dispatch-0001",
      "persona_archetype_id": "archetype-1",
      "segment_id": "marketing-leader",
      "context_stratum_id": "marketing-leader-active-evaluation",
      "profile_snapshot": {},
      "context_attribute_provenance": [],
      "worker_context_isolation": "isolated",
      "human_sample_independence": false,
      "variation_ids": ["creative-a", "creative-b", "creative-c", "creative-d"],
      "blind_labels": {},
      "shown_order": [],
      "reaction_protocol": "progressive_reveal",
      "reaction_prompts": [],
      "comparison_prompt": ""
    }
  ],
  "reaction_schema": {},
  "comparison_schema": {}
}
```

Validate the enriched jobs:

```bash
python3 scripts/validate-panel-run.py jobs.json \
  --manifest study-manifest.json \
  --audience-resolution audience/resolution.json \
  --dispatch-authority stage-dispatch-authority.json \
  --expected-count <N>
```

Do not dispatch the planner’s assignment core directly. Verify enrichment and validation end to end before collection.
For `--dispatch-authority`, use the complete frozen study plan at screening,
the screening result containing the bound `boundary_plan` at boundary, and the
approved finalist-roster decision at finalist.

## Claude Dynamic Workflow

Use `scripts/claude-large-panel-workflow.mjs` only when the Workflow capability is exposed and enabled.

The script:

- accepts one validated v3 `authenticated_jobs_envelope`, or v2
  `synthetic_replicate_jobs` paired with the producer-emitted
  `legacy_v2_origin_authority` canonical absolute path, plus
  `reaction_schema` and `comparison_schema`;
- reopens the read-only producer record, actual package-validator preflight,
  exact assignment/context/optional manifest, and complete produced-job
  evidence before accepting loose v2 jobs;
- validates exact job cardinality and provenance fields;
- calls one agent with one profile and one newly revealed creative for each reaction;
- freezes accepted reactions before constructing the comparison prompt;
- retries each malformed reaction or comparison once;
- assembles `screening_response`, `boundary_response`, or `finalist_response` records;
- returns `raw_provider_returns`, `rejected_attempts`, `validation_failures`, and `dispatch_audit`;
- reports `complete` or `incomplete` and missing replicate IDs.

It does not plan assignments, aggregate models, decide a roster, run the arbiter, or render the dashboard.

Its `responses`, `raw_provider_returns`, `rejected_attempts`, and `dispatch_audit` arrays form one lineage graph. The canonical delivery names are `panelist-responses.jsonl`, `raw-provider-returns.jsonl`, `rejected-attempts.jsonl`, and `dispatch-audit.jsonl`. Run `scripts/materialize-run-lineage.py` with the workflow output, source manifest, and run directory to write all four files and bind their paths, hashes, and record counts for dashboard Downloads. The audit, not accepted responses, owns dispatched-slot counts. Every row carries the manifest-bound retry limit, authorized call positions, and exact reaction/comparison attempt counts; the raw call set must match those counts exactly. A reaction-exhausted dispatch retains accepted concurrent reaction calls, omits comparison, and proves an exact rejected sequence from attempt 1 through the terminal retry. A comparison-exhausted dispatch retains all successful reaction calls and the exact rejected comparison sequence. Both report `incomplete` and have no accepted composite response.

Pass structured args, not a JSON-encoded string. Record workflow/run IDs and resume metadata supplied by the runtime. If the script returns `incomplete`, retain missing IDs and do not claim a complete stage.

## Codex And Other Agent Runtimes

For `independent_subagents`:

1. Size each wave to the observed active-agent limit.
2. Dispatch fresh non-forked contexts; one replicate profile per worker.
3. For immediate reactions, ensure each call contains only the newly revealed creative.
4. Validate and freeze returns before exposing the next creative or comparison.
5. Retry malformed returns once in a fresh context with the same assignment/profile.
6. Close or release completed workers as the runtime requires before the next wave.
7. Checkpoint accepted records and raw-return lineage outside worker contexts.

For `isolated_sequential_agents`, use the same contract serially. Concurrency changes elapsed time, not the unit or provenance of the response.

Never reuse a completed worker for a different replicate merely to save capacity.

## Screening Collection And Aggregation

After validation:

```bash
python3 scripts/validate-panel-run.py jobs.json \
  --manifest study-manifest.json \
  --audience-resolution audience/resolution.json \
  --dispatch-authority stage-dispatch-authority.json \
  --responses screening-responses.jsonl --expected-count <N>
python3 scripts/aggregate-screening.py screening \
  --manifest study-manifest.json \
  --jobs jobs.json \
  --responses screening-responses.jsonl \
  --dispatch-audit dispatch-audit.jsonl \
  --recovery-config references/screening-recovery-config.json \
  --output screening-model-results.json
```

The frozen jobs file is required for partial exposure as well as complete exposure. Every accepted response must bind exactly to one planned job, including study, method, stage, replicate, dispatch, roster, order, profile, segment, and any planned context-stratum ID. If every frozen job has an accepted response, `--dispatch-audit` may be omitted. If any planned job has no accepted composite response, the audit is mandatory and must prove the exact manifest-bound terminal reaction or comparison retry exhaustion for that job. Missing, duplicate, unplanned, altered, or audit-unbound records make the screening input invalid. An open collection remains `incomplete`; a closed, audit-proven exhausted collection proceeds only through the normal calibrated recovery gates.

For v3, the package, resolution envelope, stage roster, selected allocation
subset, jobs envelope, and manifest allocation-jobs index are one authorization
chain. Missing, substituted, reordered, or hash-mismatched links stop before
worker dispatch or dashboard compilation. If a full roster or selected prefix
is `allocation_distorted`, emit the bound decision document and stop. Continue
only after explicit directional approval of that exact run or subset; the claim
then downgrades to `directional_tier_1_for_this_run` without changing panelist
prompts, scoring, aggregation, or worker count.

The aggregator retains invalid/refusal outputs and exits nonzero for `invalid`. Do not ask a worker or arbiter to reproduce the estimator.

## Frozen Boundary Waves

Boundary comparisons use a separate predeclared plan with `plan_version: predeclared-boundary-v1`. Freeze the candidate IDs, clear groups, pair assignments, wave numbers, and `available_boundary_reserve` before the first pairwise dispatch.

Also freeze the audience-slot, grounded-profile, snapshot-hash, segment,
archetype, and context-stratum allocation for every authorized boundary and
finalist slot before first-round results are known. Authorize only the exact
cumulative prefix for the current wave. A round-robin implementation must still
prove that each stage preserves the approved audience allocation and required
context coverage; it may not simply cycle through whichever profiles happen to
appear first.

Dispatch exactly the currently authorized wave. Do not send later-wave jobs early. After a complete wave, run:

```bash
python3 scripts/aggregate-screening.py boundary \
  --manifest study-manifest.json \
  --screening-results screening-model-results.json \
  --responses boundary-responses.jsonl \
  --output boundary-results.json
```

Continue only when `decision_audit.next_wave_job_ids` authorizes the next wave. Stop on `resolved`, reserve exhaustion, maximum waves, predeclared job exhaustion, or an invalid protocol event. Responses generated after an inclusion stop invalidate the boundary run.

Wave 2 and later job preparation consumes all three prior-wave authorities:

```bash
python3 scripts/prepare-panel-jobs.py \
  stage-dispatch-authority.json dispatch-context.json jobs.json \
  --manifest study-manifest.json \
  --audience-resolution audience/resolution.json \
  --prior-jobs-envelope prior-wave-jobs.json \
  --prior-responses prior-wave-responses.jsonl \
  --prior-boundary-result boundary-results.json
```

The prior jobs must be the authenticated immediately preceding wave, the
responses must bind every one of those jobs, the canonical boundary result
must mark that wave complete and unresolved, and
`decision_audit.next_wave_job_ids` must exactly equal the newly authorized
frozen wave. Prior jobs alone never authorize continuation.

Every authorized boundary job consumes one predeclared boundary slot. Attempts within that job increase `total_model_calls` but do not consume another slot. `finalist_reserved` is never borrowed.

`boundary_jobs_per_wave` is an operational sequential-wave size, not a statistically validated sample size. An eight-review wave may resolve or remain unresolved only under the predeclared conditional rule and reserve. Never say eight reviews are inherently adequate. Re-instantiating the original frozen profile slots in new isolated sessions is allowed only as a separately labeled same-profile reliability diagnostic; it is not an independent panel, cannot be added to the audience size, and does not replace the default holdout boundary evidence.

## Retry, Resume, And Completeness

- Retain every raw provider return, including rejected attempts.
- Count every provider attempt in `total_model_calls`; do not treat retries as new synthetic replicates or new reserved job slots.
- Retry only schema-invalid, empty, interrupted, or assignment-mismatched returns.
- Keep the same replicate profile, blind labels, shown order, and response/dispatch identity on retry.
- Do not retry a tie, inability, unexpected winner, or unfavorable feedback.
- After the one retry fails, mark that record missing and the stage incomplete.
- On resume, dispatch only missing authorized work. Never rerun accepted records to change a distribution.

Before aggregation or synthesis, reconcile requested, dispatched, raw-return, accepted-record, accepted-unique-replicate, retried, rejected, and missing counts.

## Finalist And Heatmap Handoff

The boundary output proposes a roster; it does not approve it. Preserve `awaiting_approval` until the user records `approved` or `approved_with_override`.

After approval:

- collect fresh `finalist_response` jobs that expose every finalist;
- compile finalist metrics deterministically;
- generate or import an attention heatmap for every imagery `representation_id`;
- block rendering if any representation, hash, provenance field, or strict timestamp is missing;
- then run the arbiter from validated source files.

Keep orchestration detail in Methodology/Test details. The marketer-facing Overview reports useful evidence counts and limits without making worker mechanics the headline.

In marketer-facing language, each accepted unique run-specific synthetic replicate may be called a **synthetic panelist**. Report one complete synthetic-panelist total across screening, boundary, and finalist stages, and show the stage counts that reconcile to it. Keep the grounded reusable audience-profile count separate. Retries, rejected attempts, and provider/model calls do not create additional panelists.

Always keep **synthetic** attached to the panelist label. If later stages use
newly instantiated IDs, call them additional synthetic panelists modeled from
the same approved audience profiles; do not imply the same identities returned
unless the run actually reused frozen identities in fresh sessions. Keep the
methodological boundary in repository documentation and structured audit fields;
do not repeat a human-versus-synthetic disclaimer in routine user-facing plans
or results.
