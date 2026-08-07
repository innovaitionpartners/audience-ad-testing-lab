# Audience Panel Builder Contracts

## Contents

1. Research intake
2. Connector capability inventory
3. Source plan
4. Source candidates and scoring
5. Normalized evidence and ledger
6. Finding support
7. Synthesis matrix
8. Approved research brief and saved panel
9. Release A count semantics
10. Validity states
11. Package delivery
12. Workflow state and scoped approvals

Unknown fields fail unless a contract explicitly allows provider-native engagement keys.

## Research Intake

`audience-panel-research-intake-v1` contains exactly:

```text
schema_version, research_id, created_at, workflow_route, target_audience,
audience_type, research_depth, decision_to_support, available_inputs,
requested_or_supplied_connectors, current_language_evidence, languages,
as_of, existing_panel
```

`target_audience` contains exactly:

```text
audience, category, market, geography, buying_context, exclusions
```

Allowed `audience_type` values:

```text
b2b, consumer, workforce, technology, small_business, mixed
```

Allowed `research_depth` values:

```text
quick_directional, standard, robust
```

Allowed `workflow_route` values:

```text
create_research_backed_panel, import_authorized_audience,
refresh_existing_panel, augment_existing_panel, audit_existing_panel,
provisional_immediate_panel
```

Refresh, augment, and audit require `existing_panel` with:

```text
panel_id, version, package_sha256
```

`available_inputs` may contain:

```text
user_research, interviews_aggregate, sales_themes, support_themes,
owned_social_aggregate, social_listening_export,
first_party_evidence_package, performance_evidence_package,
last30days_json, mapped_social_export
```

Raw CRM and raw performance data are prohibited. Use Audience Data Lab to produce the two approved aggregate evidence packages.

`import_authorized_audience` is the intake route for arbitrary authorized CSV,
XLSX, JSON, or linked files. Its source plan is an approval-gated handoff plan:
it does not inspect, transform, or execute against those files. Send them to
Audience Data Lab for privacy profiling, exact mapping approval, and
deterministic transformation. Audience Panel Builder may continue only from the
resulting validated aggregate `authorized-audience-handoff-v1`; it never opens
the arbitrary source rows.

`requested_or_supplied_connectors` are hints and may contain:

```text
sprout, sprinklr, brandwatch, meltwater, talkwalker, pulsar,
authenticated_social_connector, native_web_research,
last30days_json_import, mapped_social_export, licensed_export, none
```

Provider names do not establish capabilities.

`current_language_evidence` is:

```text
required, useful, not_applicable
```

## Connector Capability Inventory

`connector-capability-inventory-v1` contains:

```text
schema_version, detected_at, runtime, connectors, unresolved_capabilities
```

Each connector contains:

```text
connector_id, provider, server_or_tool, detection_method, status,
schema_fingerprint, capabilities, scope, constraints, privacy_risk
```

Planning uses only capabilities whose status is `available_verified`. Capabilities are read-only and granular. Publishing, replying, deleting, tagging, and other mutations are forbidden.

## Source Plan

`audience-source-plan-v1` is deterministic and contains:

```text
schema_version, plan_id, created_at, intake_sha256,
capability_inventory_sha256, registry_freshness, workflow_route,
research_depth, evidence_basis, performance_context, target_audience,
decision_to_support, research_questions, lane_requirements,
selected_source_families, social_collection, evidence_acceptance,
unresolved_requirements
```

The plan selects source families and query templates. It is not evidence and contains no findings. Registry records never enter source candidates without actual retrieval and assessment.

For `import_authorized_audience`, the plan selects no source family. It records
one required, unresolved first-party handoff until Audience Data Lab returns a
validated aggregate `authorized-audience-handoff-v1`. The route carries
`evidence_basis: none` while that handoff is unresolved; the handoff's canonical
batches establish the eventual first-party aggregate evidence basis.

`registry_freshness` contains:

```text
updated_at, age_days, review_after_days, status, warning
```

`status` is `current` or `review_due`. A review-due registry does not invalidate
retrieved evidence, but every selected URL, edition, method page, and connector
assumption must be rechecked before collection.

## Source Candidates And Scoring

`audience-source-candidates-v1` contains:

```text
schema_version, plan_id, created_at, candidates
```

Each candidate contains exactly:

```text
candidate_id, source_family_id, lane, title, publisher, source_url,
methodology_url, publication_date, field_dates, population, geography,
sample_size, collection_method, access_route, reuse_status, assessments,
social_collection, upstream_source_ids, evidence_item_ids, notes
```

`evidence_item_ids` must resolve to exact items in `audience-evidence-ledger-v1`. A source registry template or source-family URL cannot satisfy this field.

`assessments` contains:

```text
audience_match, decision_match, methodology_transparency,
collection_quality, recency, geography_match, subgroup_usefulness,
permitted_use
```

`social_collection` is `null` outside the social/community lane. For social evidence it contains:

```text
platform, query, window_start, window_end, timezone, unit_of_analysis,
sort_mode, item_limit, pagination, returned_item_count, completeness,
collector, collector_version, run_or_dataset_id, deduplication_control,
bot_spam_control, engagement_available
```

Scored output is `audience-scored-sources-v1` and adds:

```text
score, tier, decision, decision_reasons
```

## Normalized Evidence And Ledger

`social-observation-batch-v1` contains:

```text
schema_version, batch_id, created_at, source_adapter, source_schema_version,
input_sha256, query, window_start, window_end, source_status, collection,
observations, coverage_warnings
```

Each observation contains:

```text
observation_id, platform, source_item_id, source_url, published_at,
collected_at, unit_of_analysis, title, text_excerpt, text_fidelity,
content_sha256, engagement, relevance_score, cluster_id, role_status,
author_group_token, freshness_verdict, json_pointer, use_constraints,
quality_flags
```

`author_group_token` is either `null` or a run-local salted token used only to assess source concentration. The raw author identifier is never emitted, and the token is omitted from the reusable panel package.

Provider-native engagement is discovery metadata only and has zero prevalence weight.

`audience-structured-evidence-batch-v1` normalizes survey, structural, public-document, and approved aggregate-handoff evidence.

`audience-evidence-ledger-v1` combines structured and social batches while retaining:

```text
schema_version, ledger_id, created_at, plan_id, imports, evidence_items,
summary
```

The ledger tracks item IDs, import hashes, permissions, source states, deduplication, upstream sources, use constraints, and rejections. The complete ledger remains a controlled audit sidecar. The portable v2 panel package contains public citations and approved aggregate private-source descriptors, not raw or person-level evidence.

## Finding Support

`audience-finding-support-v1` contains:

```text
schema_version, created_at, ledger_sha256, findings
```

Each row contains:

```text
finding_id, evidence_id, evidence_item_ids, support_role, analyst_note
```

`support_role` is `supports`, `qualifies`, or `contradicts`. Every item ID must resolve to the exact immutable ledger.

## Synthesis Matrix

`audience-synthesis-matrix-v1` is required before an evidence-backed brief. It
contains exactly:

```text
schema_version, plan_id, created_at, ledger_sha256, questions
```

Each question contains:

```text
question_id, research_question, findings
```

Each finding contains exactly:

```text
finding_id, statement, category, evidence_item_ids, supporting_item_ids,
qualifying_item_ids, contradicting_item_ids, integration_state,
methodological_limitations, relevance, coherence, adequacy, confidence,
confidence_reason, inference_boundary, marketer_implication,
creative_implications, segment_decision
```

Every item ID must resolve to the ledger and match the support role recorded for
that finding in `audience-finding-support-v1`. Allowed integration states are
`convergent`, `complementary`, `mixed`, `discordant`, and `single_source`.
Confidence components are `no_serious_concerns`, `minor_concerns`,
`major_concerns`, or `unknown`. Segment decisions are `candidate`,
`emerging_hypothesis`, `gap_only`, or `not_segment_relevant`.

## Approved Research Brief And Saved Panel

The same plugin continues to use:

- `audience-research-brief-v2`;
- `saved-audience-panel-v2`;
- `audience-panel-package-v2`.

The exact allowlists are bundled in the V2 Panel Contracts reference routed
directly from `SKILL.md`. Audience Panel Builder owns creation and approval. Ad
Testing Lab owns consumption, synthetic panelist instantiation, and scope
resolution.

Existing approved v2 packages are a **Tier 1 evidence-grounded panel**. This
is a construction-quality designation, not a population-composition, customer
survey, or human-sample claim.

## Release A Count Semantics

Construction and run counts are distinct. The Markdown review surfaces render
these exact labels and never use a reusable profile, retry, rejected provider
return, or model call as a panelist count:

```text
audience_groups
mindsets
buying_situations
reusable_profiles
requested_synthetic_panelists
response_jobs
accepted_response_records
retries
rejected_provider_returns
model_calls
```

The first four are construction counts from the approved panel. The remaining
six require both a run plan and run results; all six are `null` for a
construction-only review.

Paired run-plan and run-results inputs must each be JSON objects. The run
results must contain `usage.unique_job_slots_planned`, and it must exactly
equal the run plan's `synthetic_replicate_capacity.required_total`.

- `requested_synthetic_panelists` is the planned unique replicate/job-slot
  total at `synthetic_replicate_capacity.required_total`. Do not substitute
  `maximum_synthetic_panelists`, which is only a ceiling.
- `response_jobs` is `usage.unique_job_slots_dispatched`.
- `accepted_response_records` is `usage.accepted_response_records`.
- `retries` counts canonical raw provider returns whose `attempt_number` is
  greater than one.
- `rejected_provider_returns` is `usage.rejected_attempts`, which the lineage
  contract binds one-to-one to rejected provider returns.
- `model_calls` is `usage.total_model_calls` and includes retries and rejected
  provider returns.

When the associated records are available, the renderer cross-checks every
count against the canonical lineage arrays and rejects a mismatch rather than
guessing. It also rejects impossible relationships: dispatched jobs cannot
exceed planned slots; accepted response records cannot exceed dispatched jobs
or model calls; rejected provider returns and retries cannot exceed model
calls.

## Validity States

Release A keeps these states separate:

```text
research_integrity_status: unreviewed | passed | failed
panel_approval_status: draft | provisional | approved | needs_refresh
human_alignment_status: untested | task_validated
population_composition: not_available
calibration_status:
  state: none | retrospectively_evaluated | temporal_holdout_validated |
         prospectively_validated
  scope: named outcome, channel, audience, geography, objective, and time window
```

**Population composition not available** in Release A. Approved means the
evidence and construction were approved for the named use. It does not imply
human alignment or field prediction. A performance evidence handoff can
establish at most `retrospectively_evaluated`; one campaign does not rewrite a
panel.

## Package Delivery

The build directory contains individually openable files. Present the HTML
research report, complete HTML panel review, panel summary, and validation report before the
canonical JSON and ZIP. Include the source plan, finding support, and synthesis
matrix as technical appendices when research was run. The ZIP is an immutable
transfer and registration container, not the preview.

`panel-review-manifest-v1` binds the exact human review revision without
changing `saved-audience-panel-v2` or `saved-audience-panel-v3`. It contains:

```text
schema_version, panel_id, panel_version, review_revision, generated_at,
canonical_panel, review_outputs
```

`canonical_panel` and each `review_outputs` row contain exactly:

```text
path, media_type, sha256, bytes
```

The canonical path is `saved-audience-panel.json`. The exact review outputs
are `audience-panel-review.html` and `panel-summary.md`. `review_revision`
matches `review-vN`. The generated approval request records the exact manifest
digest and canonical-panel digest. Research-report output uses
`audience-research-report-manifest-v2`, which requires the exact
`panel-review-manifest.json` input. The blind audit binds that report manifest,
so any canonical panel, Markdown,
HTML, review-manifest, or report-manifest byte change invalidates the audit and
downstream workflow bindings.

The HTML review uses the Audience Ad Testing Lab dashboard visual system:
black structural bars, blue and mint decision surfaces, DM Serif Display for
display headings, Instrument Sans for body copy, and a 17 px desktop body
baseline. It begins with an executive reading of segments, grounded profiles,
evidence coverage, limitations, and governance, then includes the complete
canonical projection. Both the HTML and Markdown reviews include every
approved evidence-source record from the bound brief: label, direct URL,
evidence ID, type, date, collection method, confidence, usable-for scope,
permitted uses, and limits. A provisional no-research panel renders an explicit
empty source directory and must not invent a citation or link. If an older
approved brief has a null `source_url`, the renderer may recover that link only
from an accepted candidate in the supplied, audited `scored-sources.json` whose
candidate or evidence-item ID matches the brief evidence ID. If no bound link
exists, the review says `link not recorded`; it never invents or guesses one.

Ad Testing Lab consumes the exact package by hash and must not mutate its segments, archetypes, strata, profiles, weights, or response behavior.

## Workflow State And Scoped Approvals

`panel-workflow-state-v1` contains exactly:

```text
schema_version, workflow_id, panel_id, panel_version, state, updated_at,
approvals, bindings
```

`workflow_id` and `panel_id` are canonical lowercase hyphenated identifiers.
`panel_version` is a non-empty string and `updated_at` is an RFC 3339 timestamp
with a timezone. `state` is one of:

```text
draft, dogfood, provisional, approved, needs_refresh, retired
```

Only these state transitions are allowed:

```text
draft         -> dogfood | provisional | approved | retired
dogfood       -> draft | provisional | approved | retired
provisional   -> draft | approved | needs_refresh | retired
approved      -> needs_refresh | retired
needs_refresh -> draft | approved | retired
retired       -> terminal
```

Every document whose `state` is `approved` requires unique approved
`evidence_synthesis` and `panel_construction` rows, including externally
supplied state JSON. Transitioning to `approved` enforces the same invariant.
`package_registration` is a scoped approval, not a workflow-state transition.

Each `approvals` row contains exactly:

```text
scope, status, approved_by, approved_at, target_sha256, note
```

`scope` is unique within a state and is one of `evidence_synthesis`,
`panel_construction`, `dogfood`, `package_registration`, or `calibration`.
`status` is `pending`, `approved`, or `rejected`. Pending rows have empty
`approved_by` and `approved_at`; approved and rejected rows require non-empty
reviewer metadata and an RFC 3339 approval time. `target_sha256` is always an
unprefixed lowercase 64-character SHA-256 digest. Consumers must require both
an approved scope and an exact matching target digest, so an approval becomes
stale whenever the approved content changes.

`bindings` contains exactly:

```text
brief_sha256, panel_sha256, report_inputs_sha256, audit_sha256, package_sha256
```

Every binding is either `null` or an unprefixed lowercase 64-character SHA-256
digest. Canonical state bytes use the shared canonical JSON encoding; the
workflow-state digest is the unprefixed SHA-256 digest of those bytes.

Proposal, build, and registration independently validate the exact brief,
panel, evidence ledger, finding support, synthesis matrix, report manifest,
and construction audit. Each boundary requires an `approved` workflow state;
exact current brief, panel, non-null report-input, and audit bindings; an
approved `evidence_synthesis` row bound to the validated synthesis digest; and
an approved `panel_construction` row bound to the exact canonical panel-review
manifest digest. That manifest binds the current panel digest plus the exact
Markdown and HTML review bytes. Build
and registration additionally require the exact package binding and an
approved `package_registration` row bound to that package digest.

The report manifest binds the pre-audit workflow snapshot (W0). The approved
workflow supplied to proposal, build, and registration is the later snapshot
(W1): its report-input binding matches the report manifest, its audit binding
matches the external construction audit, and the external audit binds the
report manifest. Consumers must not require the sealed report to hash W1;
doing so would create a report → workflow → audit → report hash cycle.

The construction-audit validator recomputes the six available Release A input
digests and the four required `null` future bindings from supplied validated
documents. A structurally valid, document-bound audit with `result: fail`
returns `valid: true` and reports that result; validation truth is distinct
from permission to package. No package boundary may copy ledger, support,
synthesis, or report bindings from the audit under review. Proposal, build,
and registration require the document-aware audit result to pass. Registration
derives the brief and panel from one immutable validated package-byte snapshot,
and every failure occurs before canonical output or library mutation.
