# Synthesis, Dashboard, Exports, And Calibration

Use this reference after deterministic aggregation. It defines what the arbiter may synthesize, what the dashboard must show, and how mapped real outcomes remain separate.

## Arbiter Boundary

The arbiter receives validated source records plus:

- `screening-model-results.json`
- `boundary-results.json` when the partial-exposure boundary stage was used
- `finalist-results.json`
- `saliency-index.json` for imagery

The arbiter validates cross-file consistency, preserves qualitative themes, and translates technical fields into plain marketer language. It does not calculate utilities, shares, shortlist stability, boundary decisions, rubric summaries, or approval state.

Complete-exposure results come from the shipped method-aware response validator and deterministic complete-set aggregator. The arbiter may explain their conditional within-run result but never initiate collection, calculate ranks, or imply population preference.

If IDs, hashes, methods, denominators, states, or source metrics disagree, synthesis is blocked. Do not repair a broken run with prose.

## Decision And Approval States

The roster decision is exactly one of:

- `awaiting_approval`: proposed IDs may be shown as pending; finalist responses and metrics are absent.
- `approved`: the deterministically proposed roster was approved without a later override.
- `approved_with_override`: a person approved a different roster or later changed it; preserve the reason and timing.

Before approval, never say an ad moved forward, was selected, or won. Do not imply a person made a decision when no approval record exists.

After approval, finalist first-choice counts and `conditional_first_choice_share` are conditional only on accepted response records in the approved finalist set. State that base beside the result.

## Required Source Files

The dashboard compiler reads one run directory containing:

1. `study-manifest.json`
2. `creative-roster.json`
3. `panelist-responses.jsonl`
4. `screening-model-results.json`
5. `finalist-results.json`
6. `feedback-synthesis.json`
7. `boundary-results.json` when used
8. `saliency-index.json` for imagery
9. `panelist-responses.jsonl`, `raw-provider-returns.jsonl`, `rejected-attempts.jsonl`, and `dispatch-audit.jsonl` when lineage is bound

Preserve each file as a self-contained download in the dashboard. The embedded creative copy, media bytes, response records, metrics, feedback, and heatmaps must match these source exports exactly.

### Audience Research And Reusable Panel Files

For a research-backed v2 audience package, prioritize these marketer-facing downloads before technical JSON:

1. **Audience research report** — `audience-research-report.html`, readable in a browser and bound to the packaged brief and panel.
2. **Research sources spreadsheet** — `research-sources.csv`, suitable for Excel or another spreadsheet tool.
3. **Reusable audience panel** — `audience-panel-package.zip`, the immutable package that an authorized Ad Testing Lab installation can validate and register.

The dashboard remains a separate self-contained HTML file. A final review bundle may contain the dashboard HTML, research report HTML, reusable panel ZIP, and sources CSV without unpacking or rewriting the inner package ZIP. JSON downloads remain available under technical files for audit, automation, and validation; do not present JSON as the primary marketer handoff.

Safe sharing requires permission to share the underlying research, aggregate or anonymized sources, no raw CRM PII, and an unchanged package ZIP whose SHA-256 hash still matches its manifest. Never bundle the private local library directory or `index.json`. Provisional no-research packages may be exported for their immediate run but must be labeled non-reusable and must not be registered or presented as a reusable panel.

### Attempt-Lineage Delivery

The canonical collection-lineage names are `panelist-responses.jsonl`, `raw-provider-returns.jsonl`, `rejected-attempts.jsonl`, and `dispatch-audit.jsonl`. Raw returns preserve every provider call; rejected attempts preserve linked validation errors and disposition; accepted responses preserve runtime attempts and source provenance; dispatch audit rows preserve complete and incomplete slots plus exact reaction/comparison attempt counts. The shared validator reconciles every raw call to those counts. An incomplete slot has no accepted composite response and must prove an exact exhausted retry sequence at either a reaction position or comparison; a reaction-exhausted slot may still retain accepted concurrent reaction calls.

The materializer writes all four files and binds their canonical paths, SHA-256 hashes, and exact record counts under manifest `outputs`. The materializer, dashboard compiler, and standalone validator use one shared cross-file validator for attempt identity, exact raw-call/audit reconciliation, rejected coverage, accepted/exhausted dispatch outcomes, stage reserves, and call-versus-slot accounting before exposing the files in Downloads. Manifest `total_model_calls` and download record counts are derived from that reconciled raw call set.

## Denominators

Report these separately and preserve stage-specific counts:

```yaml
denominators:
  total_model_calls: 0
  accepted_response_records: 0
  accepted_unique_replicates: 0
  unique_archetypes: 0
  grounded_context_profiles: 0
  accepted_context_strata: 0
accepted_response_records_by_stage:
  screening: 0
  boundary: 0
  finalist: 0
accepted_unique_replicates_by_stage:
  screening: 0
  boundary: 0
  finalist: 0
```

`total_model_calls` includes rejected and retried provider calls. `accepted_response_records` counts complete accepted discriminated records. `accepted_unique_replicates` deduplicates their `synthetic_replicate_id` values. `unique_archetypes` and `grounded_context_profiles` describe the audience model, not response volume. `accepted_context_strata` counts distinct named context-stratum IDs represented by accepted records; it is not another response or replicate count.

## Feedback Synthesis

Every theme remains traceable:

```yaml
theme:
  stage: screening | boundary | finalist
  creative_id: ""
  segment_id: ""
  lane: copy | image | video | combined | offer_appeal | proof | attention | media_context | platform_fit
  feedback_type: strength | friction | disagreement | next_test
  evidence_scope: single_source_observation | cross_response_pattern
  theme: "specific evidence-grounded paraphrase"
  why_it_matters: "marketer-relevant consequence without a performance claim"
  recommended_action: "explicit keep, change, or test-next guidance"
  source_type: model-generated synthesis
  response_ids: []
  exposed_base:
    count: 0
    label: "accepted AI reviews exposed to this creative in this stage"
  limitations: ["nonempty evidence and interpretation limit"]
```

All fields shown above are required. Only accepted source response IDs assigned to the stated stage, creative, and segment may support a theme. `exposed_base.count` equals the full eligible accepted exposure base for that exact stage, creative, and segment; the number of supporting response IDs cannot exceed it.

Every creative with usable written reactions needs at least one `strength` or `friction` theme. Every approved top ad needs at least one `strength`, one `next_test`, and one `friction` or `disagreement` theme when the source evidence contains friction or disagreement. `evidence_scope: cross_response_pattern` needs at least two supporting response IDs; one supporting response requires `single_source_observation`. A one-response observation is allowed only when `limitations` or `recommended_action` visibly calls it single-source and the copy does not imply consensus.

`recommended_action` must say what to keep, change, or test next. A change-style action explicitly uses `test`, `try`, `compare`, `hypothesis`, or `experiment`. Any proposed change is a hypothesis to test, never a proven fix. Feedback fields must not claim customer/population preference, survey incidence or percentages, predicted/proven CTR, conversion, revenue, sales, campaign performance, guaranteed outcomes, or that a proposed change “will improve,” “proves,” or “guarantees” anything. Preserve ties, inability-to-judge responses, negative signals, disagreement, and source-specific caveats.

## Marketer-First Dashboard Contract

The primary navigation is exactly:

**Overview, Ads tested, Test audience, All ad results, Top ads, Feedback, Attention heatmap (imagery only), AI audience responses, Methodology, Downloads**

Use plain, literal primary labels. The main experience answers:

- what was tested;
- what stood out;
- which ads merit closer review or are pending;
- why;
- how much evidence was usable; and
- the limits.

The dashboard should be polished enough for a marketer to present directly: clear hierarchy, restrained visual design, readable charts, full creative previews, responsive layout, keyboard navigation, and no internal-ID-first labels. Avoid hype and agency slogans.

Do not lead primary cards, tabs, or headings with MaxDiff, Davidson, conditional stability, evidence ledger, decision surface, provenance, or audit terminology. Place exact technical content under **Methodology/Test details** or accessible keyboard/tap information popovers.

## View Requirements

### Overview

Show:

- study objective, creative count, plain method label, and validity label;
- pending or approved roster language that matches the source state;
- what stood out and why;
- the accepted unique synthetic-panelist total, the separate grounded audience-profile count, and stage-specific synthetic-panelist counts that visibly reconcile to the total;
- a direct CTA to inspect the heatmap when imagery evidence exists;
- concise run-specific limits.

The stage-specific synthetic-panelist breakdown is visible by default. Do not put any Overview content inside an accordion or click-to-expand disclosure.

The five run-integrity dimensions are Research basis, Input fidelity, Review integrity, Design adequacy, and Result stability. Keep them separate from human-alignment validation and field-performance calibration.

### Ads Tested

Show human-readable display names, exact copy, CTA, input fidelity, and every supplied media representation. Embed renderable images or frame representations; never expose local paths. Each representation keeps its stable ID and content hash in details.

### Test Audience

Show the reusable grounded panelist profiles themselves: role/title context, audience segment, buying situation, decision context, motivations, concerns, triggers, and proof needs. Use one card or row per `grounded_profile_id`; do not create one “profile” row per `synthetic_replicate_id` or per stage-specific review job. Keep internal archetype labels and evidence details secondary.

Review volume, test round, ads reviewed, shown order, reactions, and response IDs belong under **AI Audience Responses**. Do not duplicate that execution roster on **Test Audience** or call review jobs “profile variations.”

Show a substantive audience research report openly, not inside an accordion. When the study is a deterministic conformance fixture, suppress the fictional report and its marketer download instead; label the audience as a test fixture and state that no substantive audience research was conducted. Never present fixture inputs as research-backed substantiation.

### All Ad Results

For `partial_exposure_maxdiff`, use the plain labels **Overall result** and **How often it ranked among the leaders**. Put centered protocol-relative utility, MaxDiff, classifications, thresholds, and conditional stability in expanded evidence details and Methodology.

For `complete_exposure`, use **Overall result**. Do not show MaxDiff, four-ad, subset, or Davidson explanations.

Show one row per ad with its first-round result, every review stage in which it appeared, and the separately collected closer-review `overall`, `comprehension`, `relevance`, `credibility`, `offer_appeal`, and `motivation` means when they exist. Use an explicit not-collected state for ads that did not reach closer review; never render a missing score as zero. Label stage bases as accepted synthetic panelists and state that later-stage IDs are newly instantiated from the same approved audience profiles when that is true; do not imply human participants or returning identities.

This view applies only to already validated source exports. It is not evidence that the current package executed complete-exposure collection or aggregation.

Never translate a stability frequency into “liked,” “preferred,” “would choose,” or a survey-style percentage.

### Top Ads

For `awaiting_approval`, show only pending IDs/names and the approval request. `metrics_available` is false; first-choice counts, shares, rubric summaries, and testing maps are empty.

For approved states, show the approved top-ad set, the exact approval/override label, 1-5 review summaries, and **Chosen first in the closer review**. State the model-call and accepted-unique-replicate base in evidence details or Methodology.

Place the single top-ad score comparison table here, not on Overview. It has one row per approved top ad and columns for the separately collected overall score plus comprehension, relevance, credibility, offer appeal, and motivation.

### Feedback

Feedback is the verbatim-reading surface. Show every accepted ad-specific reaction and closer-review improvement note, grouped by ad, before synthesized themes. Provide an ad filter and show one ad’s complete feedback at a time by default so a large response set remains navigable; filtering must not discard or sample any accepted feedback. Each main-test or tie-break response keeps its immediate reaction, what was noticed first, strongest positive signal, and strongest negative signal together as one panelist response. Label each entry as a synthetic panelist reaction or closer-review note and show its audience context, stage, ad, and response ID. State the total number of verbatim ad reactions and closer-review notes available. Select exact source text only; do not invent composite quotations, testimonial identities, human headshots, or focus-group framing.

**AI Audience Responses** is the compact table surface for scanning and filtering those same accepted panelist–ad responses. Provide a direct path from each ad’s Feedback section to its filtered table rows. Synthesized feedback may follow the verbatim responses with the feedback type, specific theme, why it matters, and recommended action. Preserve materially dissenting responses when they exist. Put limitations, supporting-response count, eligible exposed base, stage, segment, lane, and source IDs inside a collapsed **Evidence details** control. Use marketer language without removing provenance.

### Attention Heatmap

For `static_image`, `carousel`, and `video_representation`, render one original/overlay comparison for every inspectable representation. Provide descriptive alt text, provider, method, intended target, target timestamp, reveal timestamp, categorical alignment, and limitations.

For `copy_only`, omit the tab and state **No imagery was tested.** under Methodology. An imagery study with missing, incomplete, untimely, or unhashed evidence is a hard stop before dashboard rendering.

Label the view **Attention heatmap** and explain **Where attention is likely to go**. It is predicted visual prominence, not eye tracking, model preference, or performance evidence.

### AI Audience Responses

Make all accepted verbatim ad reactions the default view, with one row per synthetic-panelist–ad exposure. Show the immediate reaction, what was noticed first, strongest positive signal, strongest negative signal, closer-review feedback when collected, and the separate comparison rationale. Do not use a comparison “Reason” column as a substitute for the ad-specific reaction fields. Provide a secondary panelist-by-panelist detail view and filters for stage, synthetic panelist ID, segment, archetype, creative, best, weakest, tie, and inability to judge. Preserve shown order, blind label, validation state, and source provenance.

### Methodology

Include the full technical record:

- method ID and exposure design;
- exact estimand and stability field;
- assignment version, seed, balance, connectedness, and resilience;
- progressive-reveal and raw-return retention controls;
- segment weighting method;
- recovery version and `calibration_status`;
- the exact version-bound planned and usable floors, including per-profile floors for profile-aware runs;
- exactly 2,000 bootstrap fits and the `0.95` successful-fit floor;
- inclusive `0.90`/`0.10` classification thresholds;
- validity state, gate results, and precedence;
- separate Davidson boundary model, frozen candidate scope, wave stop, and reserve audit when used;
- approval state and override history;
- exact denominators;
- heatmap hashes, provider/method, strict timing, categorical alignment, and limitations;
- the five run-integrity dimensions;
- human-alignment and field-calibration states;
- run-specific interpretation limits and no population inference.

Methodology is an explanation page, so its core content is visible by default rather than hidden behind generic click-to-expand controls. It must explicitly list what each synthetic panelist was asked at each stage, distinguish ad-specific verbatim reactions from comparison rationales, explain why later stages used fresh smaller panels, and state that the predeclared wave size is not a universal sample-size claim. Provide direct self-contained download links to the manifest, accepted responses, raw returns, rejected attempts, dispatch audit, statistical outputs, and other available technical-audit resources.

### Downloads

Embed each source file as a direct self-contained download. Do not fetch from the network or from local runtime paths.

Order the audience research report, sources CSV, and reusable package before technical JSON when they are available. Label the CSV as spreadsheet-ready and the ZIP as a reusable audience panel. Explain what each file is for in one literal sentence. Do not expect marketers to inspect JSON to understand the audience or the result.

## Heatmap Integrity

For imagery, `saliency-index.json` must be `available` and cover the exact set of media `representation_id` values. Each entry binds:

- `variation_id`
- `representation_id`
- original `content_hash`
- `overlay_content_hash`
- original and overlay files/MIME types
- provider and method
- `predeclared_target`
- `target_declared_at`
- categorical alignment: `aligned`, `partially_aligned`, `misaligned`, or `unclear`
- nonempty limitations

The roster `approved_at` timestamp is strictly before index `revealed_at`; each `target_declared_at` is strictly before `revealed_at`. A changed roster after reveal is a labeled saliency-informed human override. Heatmaps cannot change screening math, boundary resolution, finalist shares, rubric scores, the deterministically proposed roster, or the approved finalist roster.

## Rendering And Validation

Run:

```bash
python3 scripts/render-dashboard.py --run-dir <run-directory> --output <dashboard.html>
python3 scripts/validate-dashboard.py <dashboard.html>
```

Rendering must fail on cross-file ID/state mismatch, source-copy mismatch, false hashes, missing media coverage, unrenderable MIME types, incomplete saliency provenance, invalid timestamps, unqualified model-call percentages, hidden local paths, external assets, or runtime fetches.

## Calibration Against Real Outcomes

Human alignment and campaign performance are separate from internal run integrity.

```yaml
external_validity:
  human_alignment_validation: not_evaluated | evaluated_with_limitations | calibrated
  field_performance_calibration: none | available | calibrated
```

When an approved `audience-performance-evidence-v1` handoff and separate run-mapping notes are supplied:

1. Validate that the aggregate handoff is approved and authorizes `ad_test_calibration`.
2. Preserve the original synthetic outputs.
3. Verify mapping to run, variants, segments, channel, objective, and time window.
4. Compare directional results while documenting delivery, targeting, landing-page, attribution, sample, and lag confounds.
5. Append a calibration note to the saved audience model.
6. Recommend a new version only when repeated or high-quality evidence changes the audience model or interpretation rules.

Use one of `append_note_only`, `confidence_update`, `scoring_or_context_tuning`, or `new_panel_version_recommended`. Never force agreement or describe one campaign as universal truth.
