# Ad Testing Lab Saved Audience Panel Contract

Use this reference to consume a reusable audience panel built from an approved
research brief and to instantiate run-specific synthetic replicates from its
immutable resolved snapshot. Audience Panel Builder owns research,
construction, audit, and packaging; Ad Testing Lab begins at resolution.

## Units

- `segment`: a researched audience cluster supported by an approved segment hypothesis.
- `persona_archetype`: an evidence-backed decision mindset within one segment.
- `context_stratum`: a provenance-bound context allocation within one segment.
- `grounded_context_profile`: one explicitly supported archetype-and-stratum combination.
- `synthetic_replicate`: one run-specific response job instantiated from one grounded profile. It occupies one unique synthetic-replicate/job slot and may require multiple provider/model calls.

Synthetic replicates are not people. Multiple jobs or calls do not establish audience prevalence or human-sample independence.

## Research And Approval Gate

Panel construction accepts only an `audience-research-brief-v2` whose `status` is `approved`, whose `approval.approved_for_panel_creation` is true, and whose approval identity, timestamp, and privacy confirmation pass the deterministic validator. A target-audience phrase, draft brief, or worker recommendation is not approval.

The builder is constrained to approved findings, evidence IDs, and segment hypotheses. It cannot research, self-approve, invent support, add a segment, or extend the exact schema. A material change returns to research and approval.

`provisional_no_research` uses the deterministic provisional materializer as
Ad Testing Lab's internal helper for one immediate run. Route selection plus
the later run-plan approval replace the reusable route's research-brief and
panel-package approvals. Unsupported fields are `unknown`; expiry is assigned
automatically at no more than 30 days. The output is not a research-backed
builder output, cannot be registered, and cannot be reused.

## Exact Saved-Panel Schema

The builder emits JSON conforming exactly to `saved-audience-panel-v2`. Top-level keys are:

```text
schema_version, panel_id, panel_name, version, created_at, updated_at,
audience_scope, persona_research, segments, persona_archetypes,
context_strata, grounded_context_profiles, replicate_strategy,
calibration_history, refresh_conditions, governance
```

Nested allowlists:

- `audience_scope`: `audience`, `market`, `geography`, `category`, `buying_context`, `exclusions`, `scope_fingerprint`
- `persona_research`: `brief_id`, `mode`, `status`, `approved_at`, `expires_at`, `source_types`, `evidence_ids`, `coverage`, `evidence_gaps`, `source_state`
- `segments[]`: `segment_id`, `name`, `origin`, `study_weight`, `weighting_rule`, `weight_source_evidence`, `finding_ids`, `evidence_ids`, `description`, `primary_needs`, `primary_objections`, `creative_implications`
- `persona_archetypes[]`: `persona_archetype_id`, `segment_id`, `display_name`, `role_context`, `decision_context`, `motivations`, `anxieties`, `triggers`, `objections`, `proof_needs`, `finding_ids`, `evidence_ids`, `evidence_strength`, `inference_boundary`
- `context_strata[]`: `context_stratum_id`, `segment_id`, `planned_weight`, `weighting_rule`, `dimensions`; dimensions contain `name`, `value`, `status`, `source_evidence`, `finding_ids`
- `grounded_context_profiles[]`: `grounded_profile_id`, `segment_id`, `persona_archetype_id`, `context_stratum_id`, `profile_snapshot`, `context_attribute_provenance`
- `profile_snapshot`: `role_context`, `decision_context`, `motivations`, `anxieties`, `proof_needs`
- provenance rows: `attribute`, `value`, `status`, `source_evidence`, `finding_ids`
- `replicate_strategy`: `worker_unit`, `shared_context_fallback_allowed`, `fields_allowed_to_vary`, `fields_never_to_invent`
- `calibration_history[]`: `date`, `source_type`, `mapped_run_id`, `mapped_variants`, `mapped_segments`, `objective`, `time_window`, `data_quality`, `directional_alignment`, `action`, `what_was_learned`, `next_run_guidance`
- `refresh_conditions`: `review_after`, `max_age_days`, `triggers`
- `governance`: `pii_policy`, `allowed_uses`, `excluded_uses`, `privacy_confirmation`

`scope_fingerprint` is computed by the shipped deterministic implementation from normalized audience, category, market, geography, and buying context. Do not hand-author it.

## Evidence And Combination Rules

- Each research-backed segment resolves to its approved hypothesis. Its finding IDs are an approved subset, and its evidence IDs resolve through those findings.
- Archetype traits use archetype-level finding and evidence provenance and preserve an inference boundary.
- Every context dimension and grounded-profile variation is `observed`, `estimated`, or `experimental` and has resolving finding and evidence IDs.
- Do not create an implicit archetype-by-stratum cross-product. Only explicit `grounded_context_profiles` may enter dispatch.
- Segment and stratum weights require a named rule and source. Unsupported composition uses a labeled planning allocation, not a prevalence claim.
- Raw person-level and sensitive source data are prohibited throughout
  segments, archetypes, strata, profiles, and governance. Evidence-grounded
  synthetic sensitive-audience concepts are permitted; unsupported stereotypes
  are not.

Run both `validate_saved_panel` and `validate_audience_research_pair` before package construction. Validation failure is a stop, not permission to invent missing support.

## Replicate Construction

The run-local resolver, not a prompt, supplies the frozen `audience_lock`,
context strata, and grounded profiles. A v3 envelope additionally binds the
exact package ZIP and manifest, composition-to-saved-profile join, usable
population-frame result when one exists, per-profile structural and conditional
weights, must-cover groups, profile snapshot hashes, and
`structural_frame` or `directional_planning` allocation basis. Tier 1 has no
frame-fidelity claim; its reusable profiles remain directional.

Tier 3 additionally requires the packaged authorized-runtime authority: the
actual approved aggregate handoff and each selected structural observation
batch. Package validation and resolution require authorized access and exact
handoff, batch/output hash, cohort, unit, and denominator parity. A public
relabel or cohort substitution is not a valid Tier 3 authority even when the
other documents are coherently resealed.

The planner materializes immutable screening, boundary-reserve, and
finalist-reserve profile rosters from that envelope. Complete exposure carries
an explicit not-applicable boundary record instead of inventing a reserve.
Every partial boundary/finalist authorization is a prefix of its pre-frozen
roster. The exact selected subset, not the unused full reserve, is the
run-claim authority shown in final outputs.

Each job receives stable study, response, replicate, and dispatch IDs; one exact
audience slot, grounded-profile ID, profile snapshot hash, segment, archetype,
stratum, and profile snapshot; complete context provenance; assigned creative
IDs, blind labels, and shown order; `worker_context_isolation`; and
`human_sample_independence: false`. Package, envelope, roster, selected subset,
and job bindings must all validate before the job is worker-ready.

One unique synthetic-replicate/job slot may require multiple provider/model calls. Retries increase `total_model_calls` but do not consume another unique slot. Worker variation must stay inside `fields_allowed_to_vary` and may never alter `fields_never_to_invent`.

## Registration, Reuse, And Refresh

After validation, build the deterministic portable package. Register only a user-approved research-backed package. Registration is immutable and idempotent for identical bytes; changed research or panel content requires a new semantic version.

Every test resolves either an approved library version or approved portable
package into the run-local `audience/snapshot`, then plans from the resulting
resolution. The library checkout is never used directly during planning or
dispatch. Reusing identical package bytes across studies creates new
study-bound immutable allocations; it does not rebuild or mutate the panel.

Calibration records append to a new panel version; they never rewrite a registered version. Refresh when the scheduled review date or age limit passes, the audience scope or buying context changes, an explicit trigger occurs, or calibration evidence changes the model.

## Validation Checklist

- Approved brief identity and privacy confirmation are exact.
- All keys match the exact schema and all IDs resolve.
- Segments, archetypes, strata, and grounded profiles preserve approved evidence boundaries.
- No unsupported joint profile or prevalence claim is introduced.
- Weight provenance, governance, refresh rules, and replicate limits are explicit.
- The validated package is approved before registration.
- Provisional packages never enter the reusable library.
