---
name: audience-ad-testing-lab
version: 1.0.0
license: Apache-2.0
description: Research-backed synthetic ad screening for existing copy-only, static-image, carousel, and represented-video variations. Use when the user wants model-conditional complete-set screening for 2-6 creatives, partial-exposure large-library screening for 7-100 creatives, a saved audience model, structured AI reactions, a marketer-ready HTML dashboard, platform or placement analysis when requested, automatic post-approval attention heatmaps for inspectable imagery, or calibration against real campaign data. Trigger for requests such as "which ad should enter a live test?", "screen this creative library", or "test these ads with CFO profiles." Do not use for net-new ad generation, media buying, or claims about human-market response.
---

# Ad Testing Lab

## Purpose

The package evaluates 2-100 existing ad variations with evidence-grounded synthetic audience profiles. It uses complete-set first-round review for 2-6 creatives and partial-exposure screening for 7-100 creatives. Produce model-conditional direction, an auditable shortlist process, a marketer-ready HTML dashboard, and source exports. This skill does not create human research, forecast campaign performance, or infer a population preference.

Synthetic replicates are not people. A fresh worker context reduces cross-response leakage, but it does not create human-sample independence. Every response record must state `human_sample_independence: false` and identify its `worker_context_isolation` state.

## Non-Negotiable Runtime Contract

- Canonical creative formats are `copy_only`, `static_image`, `carousel`, and `video_representation`. One study uses exactly one format.
- Supported method IDs are `complete_exposure` and `partial_exposure_maxdiff`. Record the method ID everywhere.
- `complete_exposure` is executable for 2-6 creatives. New v3 runs use the versioned profile-aware capacity planner, frozen balanced reserves, progressive reveal, exact complete-set rankings, and whole-record bootstrap within grounded profile. Frozen v2 runs retain their original nine-per-segment interpretation. It never uses MaxDiff, four-item subset, boundary-candidate, or Davidson fields.
- `partial_exposure_maxdiff` uses four-item, near-balanced assignments for 7-100 creatives after the burden pilot passes.
- Response records are discriminated as `screening_response`, `boundary_response`, or `finalist_response`.
- Use one context-isolated synthetic replicate per worker where the runtime supports it. Shared-context fallback must be labeled and cannot support the same review-integrity claim.
- Any reaction labeled `immediate` requires orchestration-enforced `progressive_reveal`. Prompt wording alone is insufficient.
- Retain accepted and rejected attempt lineage, validation errors, attempt IDs, accepted source provenance, and every dispatched slot. Materialize `panelist-responses.jsonl`, `raw-provider-returns.jsonl`, `rejected-attempts.jsonl`, and `dispatch-audit.jsonl`; bind canonical paths, SHA-256 hashes, and record counts under manifest `outputs`; and include all four in dashboard Downloads. Bind each audit row to the manifest retry policy, authorized call positions, and exact `reaction_attempts` and `comparison_attempts` counts; the raw provider-call set must reconcile exactly to those counts. When one concurrent reaction exhausts the exact full retry sequence, retain accepted component calls from the other positions, omit comparison, mark the job incomplete, and emit no accepted composite response. When every reaction succeeds but comparison exhausts, retain the full comparison retry sequence and likewise emit no accepted composite response. Never retry merely because a response is surprising.
- `maximum_synthetic_panelists` caps unique synthetic-replicate/job slots, not a provider/model-call ceiling. Progressive-reveal stages increase `total_model_calls`. Retries and rejected attempts increase `total_model_calls` but do not consume another unique job slot.
- Planner and aggregator CLIs own quantitative decisions. Prompts collect structured reactions and synthesize prose; they never calculate utilities, shares, shortlist stability, or boundary decisions.
- Report separate denominators: `total_model_calls`, `accepted_response_records`, `accepted_unique_replicates`, `accepted_response_records_by_stage`, `accepted_unique_replicates_by_stage`, `unique_archetypes`, `grounded_context_profiles`, and `accepted_context_strata`. On every marketer-facing run plan and result, translate those into the distinct labels **grounded profiles**, **planned isolated synthetic executions**, **minimum usable feedback records**, **accepted feedback records**, and **model calls**. Keep **synthetic** attached to panelist and execution labels. Never use one count as a substitute for another.
- Report `valid`, `exploratory`, `invalid`, or `incomplete` for screening validity; `resolved`, `unresolved`, or `invalid` for boundary status; and `awaiting_approval`, `approved`, or `approved_with_override` for the finalist roster.
- Model-call outputs are conditional on this protocol and run. Make no population inference and never present synthetic percentages as survey, customer, or market evidence.

## Automatic Attention-Heatmap Rule

Automatically generate or import one attention heatmap for every inspectable media representation after the deterministically proposed finalist roster is approved. This applies to `static_image`, every card in `carousel`, and every supplied thumbnail, keyframe, or video-frame representation in `video_representation`.

`copy_only` is the sole normal omission route. Its Methodology state must say: **No imagery was tested.** Do not render an empty heatmap tab.

For imagery, missing, incomplete, unhashed, or untimely saliency evidence is a hard stop before dashboard rendering. Every entry must bind `representation_id`, original `content_hash`, `overlay_content_hash`, provider, method, a predeclared attention target and timestamp, reveal timestamp, categorical alignment, and nonempty limitations. Target declaration must be strictly before reveal; reveal must be strictly after roster approval. The heatmap is downstream diagnostic evidence and cannot alter screening math, boundary resolution, finalist shares, rubric scores, the deterministically proposed roster, or the approved finalist roster. A later roster change is a labeled human override.

## Routing

Read only the files required for the current phase:

| Need | Read or run |
|---|---|
| Install deterministic screening dependencies when absent | `python3 -m pip install -r requirements-screening.txt` |
| Intake, formats, representations, or study request | `references/input-contracts.md` |
| New or materially updated audience model | Return the Audience Panel Builder handoff requirements to the user or outer orchestrator; do not invoke another skill |
| Saved audience model, archetypes, and context strata | `references/panel-contract.md` |
| Legacy v2 research-brief compatibility | `agents/audience-researcher-prompt.md` |
| Legacy v2 panel-construction compatibility | `agents/audience-panel-builder-prompt.md` |
| Job and response records | `references/review-contracts.md` |
| Method-specific scoring and validity | `references/scoring-rubric.md` |
| Large-library planning and worker orchestration | `references/large-panel-orchestration.md` |
| Heatmap generation/import and evidence schema | `references/visual-attention-saliency.md` |
| Synthesis, dashboard, exports, and calibration | `references/synthesis-dashboard-calibration.md` |
| Validate approved aggregate performance evidence | `python3 scripts/validate-performance-evidence.py <handoff.json>` |
| One synthetic response worker | `agents/persona-reviewer-prompt.md` |
| Platform-fit review when explicitly requested | `agents/platform-specialist-prompt.md` |
| Post-aggregation synthesis | `agents/arbiter-prompt.md` |
| Performance calibration | `agents/calibration-analyst-prompt.md` |
| Fictional worked shapes | `references/examples.md` |

## Response Modes

- `preflight_intake`: request only blocking audience, decision, creative, research, or representation inputs.
- `research_brief_gate`: present the research brief. Do not create archetypes or dispatch model calls.
- `run_plan_gate`: present method, assignment, capacity, orchestration, representation, and heatmap plan for approval. Do not dispatch.
- `screening_collection`: collect and validate first-round responses. Do not infer results in the prompt.
- `boundary_collection`: dispatch only the next authorized, predeclared pairwise wave.
- `finalist_approval_gate`: show the deterministic proposed roster as `awaiting_approval`. Do not say ads moved forward or expose finalist metrics.
- `finalist_collection`: after approval, collect complete-set 1-5 finalist reviews in fresh contexts.
- `heatmap_collection`: after roster approval, generate or import required evidence for every representation.
- `final_dashboard`: render, validate, and deliver the self-contained dashboard and source exports.

## Workflow

### 1. Lock The Brief And Research Basis

Collect the primary decision, campaign goal, funnel stage, success metric, target audience, market/buying context, selected platform mode, one canonical creative format, creative roster, requested shortlist size, the ceiling on unique synthetic-replicate/job slots, and any performance data. This ceiling is not a provider/model-call ceiling; staged calls and retries are counted separately.

Choose exactly one audience route before method planning.

**New-audience handoff — reusable route:** this skill does not research or construct a reusable audience and must not invoke another skill. When the user requests a new or materially changed reusable audience model, stop before run planning and return the exact handoff needed from Audience Panel Builder:

- one approved `audience-panel-package-v2` or
  `audience-panel-package-v3` ZIP, or an immutable registered panel version;
- its marketer-readable audience research report and panel summary for review;
- the package approval identity and timestamp.

The user or outer orchestration layer runs Audience Panel Builder separately and supplies the finished package. If row-level CRM or customer data is involved, the user or outer orchestration layer first runs Audience Data Lab and supplies only its approved aggregate handoff to Audience Panel Builder. Ad Testing Lab resumes at **run-local resolution → planning** and never sees raw private rows, improvises research, or modifies the approved audience package. This reusable route is distinct from the run-local provisional route below.

For an honest legacy v2 migration, the v3 package must embed both canonical
`migration-provenance.json` and the original validated v2 archive. A
`legacy_v2_migration` marker or null workflow/audit documents alone are never
runtime authority.

Every Tier 3 package must also embed
`authorized-audience-runtime-authority.json`. It contains the actual approved
aggregate handoff plus each selected structural observation batch and binds
their hashes, authorized access route, unit/denominator, and exact cohort.
The package builder and run-local resolver both revalidate that authority.

The retained audience researcher and panel-builder prompts are v2 compatibility surfaces for existing external orchestration. They are not workers dispatched by this skill's new-audience route.

**Saved-panel route:** `audience_panel` selects an immutable library version or portable ZIP, then follows **resolution → planning**. Do not rebuild research, read directly from the library during dispatch, or accept free-form audience profiles.

**Provisional route:** When creatives are supplied, Ad Testing Lab owns the user-facing provisional route. Use `provisional_audience` when the user wants to test those creatives now without audience research; do not send the user through a separate Audience Panel Builder approval workflow. Treat Panel Builder's provisional materializer as an internal run-local materialization helper, not a user-facing skill handoff.

The user's choice to proceed without research selects the route; it does not create an additional approval surface. There is **no research-plan approval**, **no research-brief approval**, and **no panel-package approval**. Convert the supplied audience description into the smallest bounded run-local scope: create one segment for each cohort the user explicitly distinguishes and one grounded profile for each materially distinct role or context the user explicitly supplies inside that cohort; if the user supplies only one audience phrase, create exactly one segment and one grounded profile. Never invent extra profiles merely to make the panel look richer. Set every unsupported field to `unknown`, and automatically set expiry from materialization time to no more than 30 days. Do not ask the user to approve placeholder evidence, unsupported persona detail, empty sources, or a package that cannot be reused. The run plan is the only approval gate for provisional audience materialization before first dispatch; later method-required finalist approval remains unchanged.

Materialize the fully labeled immediate-run package and canonical run-local resolution **without registration** only after creatives are available. Never register or reuse it, never resolve it through the saved-panel route, and never describe it as research-backed or as observed human behavior. A later reusable panel starts again with Audience Panel Builder's research and approval workflow.

If no creatives are supplied, retain only a draft audience scope for a future run and do not materialize a provisional panel. Ask for creatives or, only if the user wants a reusable research-backed audience, return the new-audience handoff above.

Context attributes must be `observed`, `estimated`, or `experimental`, cite resolving evidence for research-backed routes, and avoid unsupported joint combinations. A target-audience phrase alone is not a research basis.

For a saved or provisional package, resolve it into the run's canonical `audience/snapshot` with:

```bash
python3 scripts/manage-audience-library.py resolve \
  <audience-intake.json> <study-scope.json> <run-directory>
```

Pass only the canonical `audience/resolution.json` to the planner. Planning and
dispatch may consume only this run-local resolution. A v3 resolution is an
approval-bound envelope: it binds the exact package bytes, audience lock,
context strata, saved grounded profiles, composition weights, and allocation
basis. It does not turn a modeled profile into a customer or human respondent.

### 2. Normalize And Hash Creative Inputs

Assign stable variation IDs and preserve exact copy. For every inspectable image, carousel card, thumbnail, keyframe, or supplied video frame, assign a stable `representation_id`, resolve a renderable image file, and record a `sha256:` `content_hash`. `video_representation` means the supplied representations are tested; do not imply the raw video was inspected unless it was.

If an imagery format lacks inspectable imagery, stop intake and request it. Do not silently downgrade an imagery study to copy-only. Only a genuine `copy_only` study follows the no-imagery route.

### 3. Choose And Freeze The Method

- For 2-6 creatives, use `complete_exposure`. For v3 audiences, the planner chooses the smallest frozen-weight-compatible profile allocation that meets the experimental per-profile usable floor and reserves only predeclared balanced profile blocks; it reserves no boundary slots. Do not choose capacity from segment count alone.
- For 7-100 creatives, use `partial_exposure_maxdiff` when the burden pilot passes. The planner must run before screening fan-out. A failed or unrun burden pilot produces `split_required`; narrow or split the study rather than weakening the protocol.

For the large-library route, run:

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

The plan reserves unique synthetic-replicate/job slots for screening, boundary,
and finalist stages before dispatch. For v3, it also freezes the exact grounded
profile on every stage slot and binds the three rosters to the resolved
envelope. `boundary_reserved = boundary_jobs_per_wave * boundary_waves_max`;
`finalist_reserved` is binding and cannot be consumed by screening or boundary
work. Retries and rejected attempts increase `total_model_calls` but do not
consume another unique job slot. When the request supplies `context_strata`,
their segment and stratum IDs are authoritative: `--reported-segments` must
equal the number of distinct supplied segment IDs, and those IDs must survive
planning, dispatch, aggregation, and dashboard delivery unchanged. Synthesize
`segment-N` IDs only when no context strata were supplied. New v3 complete-
exposure plans report core and balanced reserve executions by grounded profile.
The shipped profile-aware floor is experimental pending 24/30/36 repeatability
calibration; it is not proof of human-sample adequacy. Frozen v2 plans retain
their original nine-planned/eight-usable segment policy.

Present the marketer-facing counts separately as grounded profiles, planned isolated synthetic executions, minimum usable feedback records, accepted feedback records (zero before collection), and model calls (planned range before collection). Pause for approval of the frozen study plan, audience lock, assignments, capacity, exposure order, worker mode, retry rule, cost/latency range, and intended attention target for each representation. For a provisional no-research run, this is the only audience-construction approval gate before initial collection; method-required finalist approval still applies later.

### 4. Build Jobs And Collect Responses

Build `synthetic_replicate_jobs` with stable study, response, replicate, and
dispatch IDs; the exact frozen audience slot, grounded-profile ID and profile
snapshot hash; profile snapshot; `context_attribute_provenance`;
segment/archetype IDs; assigned variations; blind labels; shown order; reaction
protocol; and rendered reaction/comparison prompts. Before any worker call,
validate the package, resolution envelope, stage roster, selected prefix,
allocation decision, and enriched job as one chain. A distorted allocation or
selected prefix stops before dispatch unless the exact run/subset receives the
explicit directional-use approval.

Validate jobs before dispatch:

```bash
python3 scripts/validate-panel-run.py jobs.json \
  --manifest study-manifest.json \
  --audience-resolution audience/resolution.json \
  --dispatch-authority stage-dispatch-authority.json \
  --expected-count <N>
```

For v3, pass that same validated whole envelope to the workflow as
`authenticated_jobs_envelope`; never extract and dispatch its jobs array.
For legacy v2, have `prepare-panel-jobs.py` emit a separate
`legacy-v2-origin-authority.json` and its read-only sibling evidence directory.
Pass the record to validation with `--legacy-v2-origin-authority`. Pass its
canonical absolute path, not its parsed contents, to the workflow as
`legacy_v2_origin_authority`; the workflow reopens the producer-managed record,
actual package evidence, exact source inputs, and complete produced jobs.
Loose jobs without that independent producer evidence fail closed.

Use `scripts/claude-large-panel-workflow.mjs` when the Claude Workflow capability is actually available. In Codex, use bounded waves of fresh non-forked subagents. Other runtimes may use fresh sequential contexts. Collect a method-specific `screening_response`: `complete_exposure` records a complete-set ranking for all 2-6 assigned creatives, while `partial_exposure_maxdiff` records one best-and-weakest choice from exactly four. Collect `boundary_response` only for authorized partial-exposure pairs, and collect `finalist_response` for the approved complete finalist set. Validate every accepted response against its exact job.

### 5. Aggregate First-Round Screening

For `complete_exposure`, bind the frozen full-roster jobs and the versioned conditional-run calibration policy:

```bash
python3 scripts/aggregate-screening.py screening \
  --manifest study-manifest.json \
  --jobs screening-jobs.json \
  --responses screening-responses.jsonl \
  --recovery-config references/complete-exposure-profile-stratified-config.json \
  --output screening-model-results.json
```

The manifest must bind `model.complete_exposure_calibration_version` to the supplied policy. A new v3 run must match its dynamic profile allocation, meet every frozen per-profile usable floor, weight profile summaries before segment summaries, run 2,000 seeded whole-record bootstrap resamples within grounded profile, pass both grounded-profile and archetype sensitivity when the locked scope contains more than one such unit, and meet the `>=0.90` finalist and `<=0.10` nonfinalist inclusion gates. For an honestly single-profile provisional scope, leave-one-profile and leave-one-archetype sensitivity are explicitly `not_applicable`, not failed or silently fabricated; the result remains conditional on that one modeled context and cannot support cross-profile or broader-audience heterogeneity claims. Duplicate or near-duplicate responses cannot create extra stability units. A closed run that misses a stability, usable-floor, disagreement, or applicable sensitivity gate is `exploratory`, stays unresolved, and proposes no roster. Frozen v2 manifests continue to use `references/complete-exposure-calibration-config.json` and their original nine/eight semantics. Neither policy establishes human-market calibration or population uncertainty.

For `partial_exposure_maxdiff`, the aggregator must run before arbiter synthesis:

```bash
python3 scripts/aggregate-screening.py screening \
  --manifest study-manifest.json \
  --jobs screening-jobs.json \
  --responses screening-responses.jsonl \
  --dispatch-audit dispatch-audit.jsonl \
  --recovery-config references/screening-recovery-config.json \
  --output screening-model-results.json
```

Every accepted partial-exposure response must match one frozen job exactly. For a v3 audience, the frozen jobs must jointly bind grounded-profile assignment and creative blocks. Aggregation derives each profile's planned creative participations from those jobs, requires the version-bound planned and usable floors inside every profile, requires the comparison graph to remain connected after removing any one grounded profile, weights the joint model with frozen profile weights reconciled to segment weights, and bootstraps whole records within grounded profile. These are recovery and coverage gates, not a universal sample-size formula and not authority for profile-level utilities or profile-level shortlist claims. If the planner cannot produce a profile-conditioned design under the authorized ceiling, stop as `split_required` or request a larger ceiling; do not fall back to segment-only allocation. The audit is required whenever a frozen job has no accepted composite response and may authorize only a manifest-bound terminal reaction or comparison retry exhaustion; it cannot authorize an unplanned response or a changed assignment. The deterministic estimator reports centered protocol-relative log utility and conditional within-run stability. Inclusive product thresholds are `>= 0.90` for `clear_finalist`, `<= 0.10` for `clear_non_finalist`, and strictly between for `boundary_candidate`. Production bootstrap count is exactly 2,000 with a successful-fit floor of `0.95`.

Validity precedence is strict: incomplete takes precedence while collection is open; disconnected or unidentified models are invalid; an identified closed run is `valid` only when every required calibrated recovery gate passes; otherwise it is `exploratory`. The shipped recovery configuration is `exploratory_only`, so it cannot produce a `valid` claim before calibration.

### 6. Resolve Only The Frozen Boundary

Freeze the first-round clear groups and boundary candidates. Do not pool MaxDiff utility with pairwise utility. Dispatch only authorized waves from the frozen boundary plan, then run:

```bash
python3 scripts/aggregate-screening.py boundary \
  --manifest study-manifest.json \
  --screening-results screening-model-results.json \
  --responses boundary-responses.jsonl \
  --output boundary-results.json
```

The separate connected Davidson model reports centered pairwise utility and conditional boundary-slot inclusion frequency on its own scale. It stops only under the predeclared inclusion rule or when the authorized plan/reserve ends. `unresolved` is a valid refusal state; a person may later approve a labeled override, but the prompt may not manufacture a boundary decision.

### 7. Approve And Collect Finalist Reviews

Before approval, set `roster_decision.status: awaiting_approval`; keep finalist responses and metrics empty; describe all roster entries as pending. Only `approved` or `approved_with_override` authorizes finalist collection and decision language.

After approval, dispatch fresh `finalist_response` jobs through the approval-and-manifest adapter. Each synthetic replicate sees every approved finalist, records a 1-5 whole-number rubric for each, and returns an exact final ranking. Then run `aggregate-screening.py finalists` with the manifest, frozen finalist jobs, screening results, approval, responses, and boundary results when a partial-exposure boundary was used. First-choice shares are conditional only on accepted records in the approved finalist set. Keep accepted records, dispatched job slots (including exhausted slots), unique accepted replicates, and total model calls separate; do not reinterpret any as population incidence.

### 8. Generate Or Import Every Required Heatmap

After approval, and strictly after the intended target was declared, create `saliency-index.json`. Generate or import an original/overlay pair for every inspectable representation. Validate the original and overlay bytes against their hashes, provider/method provenance, strict timestamps, categorical alignment (`aligned`, `partially_aligned`, `misaligned`, or `unclear`), and nonempty limitations.

If any imagery representation is missing valid evidence, stop. A manual observation may be preserved as a clearly labeled heuristic outside evidence scoring, but it cannot satisfy the heatmap contract.

### 9. Synthesize Without Recalculating

Give the arbiter validated source records plus deterministic `screening-model-results.json`, `boundary-results.json` when applicable, and `finalist-results.json`. The arbiter may validate consistency, preserve dissent, organize feedback, and translate technical fields into marketer-facing language. It may not recalculate, rerank, choose finalists, or change approval state.

### 10. Render, Validate, And Deliver

Render only from complete, cross-file-consistent source exports:

```bash
python3 scripts/render-dashboard.py --run-dir <run-directory> --output <dashboard.html>
python3 scripts/validate-dashboard.py <dashboard.html>
```

The primary navigation must read exactly: Overview, Ads tested, Test audience, All ad results, Top ads, Feedback, Attention heatmap (imagery only), AI audience responses, Methodology, Downloads.

Use plain, literal primary labels. The main view answers what was tested, what stood out, what moved forward or is pending, why, how much evidence was usable, and the limits. Keep MaxDiff, Davidson, formulas, provenance, exact denominators, validity gates, run-integrity dimensions, and run-specific limitations under **Methodology/Test details** or accessible keyboard/tap information popovers. Do not lead the dashboard with research jargon or agency slogans.

Preserve downloadable source lineage for the manifest, creative roster, accepted responses, raw provider returns, rejected attempts, dispatch audit, screening results, boundary results when used, finalist results, feedback synthesis, and saliency index for imagery. The manifest binds all four response/call lineage files under `outputs` with canonical paths, SHA-256 hashes, and exact record counts. One shared lineage validator must serve materialization, rendering, and standalone dashboard validation, and it must reconcile every raw reaction and comparison call to the audit's exact attempt counts. A reaction- or comparison-exhausted job has no accepted composite response; dispatched counts come from the audit, while total model calls and download record counts come from the reconciled raw call set.

## Run-Integrity Dimensions

Every dashboard reports these five dimensions without collapsing them into one score:

1. Research basis
2. Input fidelity
3. Review integrity
4. Design adequacy
5. Result stability

Human-alignment validation and field-performance calibration are separate evidence lanes. A strong internal run does not establish either one.

Calibration accepts only an approved `audience-performance-evidence-v1` handoff whose allowed uses include `ad_test_calibration`. The user or outer orchestration layer obtains that aggregate file from Audience Data Lab and supplies it separately. Validate it with `validate-performance-evidence.py`; do not read row-level campaign, CRM, account, or customer data in this skill.

## Do Not

- Do not create or rewrite ads unless the user separately asks after the evaluation.
- Do not call synthetic replicates respondents, consumers, customers, survey participants, or a market sample.
- Do not infer human-sample independence from worker isolation.
- Do not flatten `complete_exposure` and `partial_exposure_maxdiff` into one method.
- Do not let prompts calculate utilities, shares, shortlist membership, stability, or boundary outcomes.
- Do not consume reserved finalist capacity elsewhere.
- Do not expose finalist metrics or “moving forward” language while approval is pending.
- Do not show heatmaps before roster approval or use them to alter synthetic-response-derived outcomes.
- Do not render an imagery dashboard without complete per-representation attention evidence.
- Do not claim thumb-stop, watch-through, CTR, conversion, pipeline, or revenue impact without mapped real performance data.
- Do not read or store raw CRM, account, customer, or performance rows in this skill, its saved audience files, or fixtures.
