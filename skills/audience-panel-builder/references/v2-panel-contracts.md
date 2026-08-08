# Shared V2 Audience Contracts

## Contents

1. Research brief
2. Saved audience panel
3. Evidence and combination rules
4. Package

These allowlists are co-shipped with the deterministic validators in the same
plugin. Unknown keys fail.

## Research Brief

`audience-research-brief-v2` top-level keys:

```text
schema_version, brief_id, created_at, updated_at, status, target_audience,
research_mode, research_depth, research_questions, evidence_sources, findings,
coverage, segment_hypotheses, evidence_gaps, privacy_confirmation, approval
```

Nested keys:

- `target_audience`: `audience`, `category`, `market`, `geography`,
  `buying_context`, `exclusions`
- `evidence_sources[]`: `evidence_id`, `type`, `source_label`, `source_url`,
  `collection_method`, `date`, `confidence`, `usable_for`, `permitted_uses`,
  `limits`
- `findings[]`: `finding_id`, `evidence_ids`, `statement`, `category`,
  `confidence`, `inference_boundary`, `creative_implications`
- `coverage`: `pain_points_challenges`, `motivations_goals`,
  `decision_criteria`, `buying_triggers`, `fears_objections`, `proof_needs`,
  `media_behaviors`; each is `strong`, `thin`, or `empty`
- `segment_hypotheses[]`: `segment_id`, `name`, `origin`, `finding_ids`,
  `evidence_ids`, `confidence`, `why_it_matters_for_ad_testing`
- `evidence_gaps[]`: `gap`, `impact_on_panel`, `mitigation`
- `privacy_confirmation`: `confirmed`, `confirmed_by`, `confirmed_at`, `note`
- `approval`: `approved_for_panel_creation`, `approved_by`, `approved_at`,
  `approval_note`

Only the user approves the brief. After approval, only `status`, `updated_at`,
and `approval` may change.

## Saved Audience Panel

`saved-audience-panel-v2` top-level keys:

```text
schema_version, panel_id, panel_name, version, created_at, updated_at,
audience_scope, persona_research, segments, persona_archetypes,
context_strata, grounded_context_profiles, replicate_strategy,
calibration_history, refresh_conditions, governance
```

Nested allowlists:

- `audience_scope`: `audience`, `market`, `geography`, `category`,
  `buying_context`, `exclusions`, `scope_fingerprint`
- `persona_research`: `brief_id`, `mode`, `status`, `approved_at`,
  `expires_at`, `source_types`, `evidence_ids`, `coverage`, `evidence_gaps`,
  `source_state`
- `segments[]`: `segment_id`, `name`, `origin`, `study_weight`,
  `weighting_rule`, `weight_source_evidence`, `finding_ids`, `evidence_ids`,
  `description`, `primary_needs`, `primary_objections`,
  `creative_implications`
- `persona_archetypes[]`: `persona_archetype_id`, `segment_id`,
  `display_name`, `role_context`, `decision_context`, `motivations`,
  `anxieties`, `triggers`, `objections`, `proof_needs`, `finding_ids`,
  `evidence_ids`, `evidence_strength`, `inference_boundary`
- `context_strata[]`: `context_stratum_id`, `segment_id`, `planned_weight`,
  `weighting_rule`, `dimensions`
- dimensions: `name`, `value`, `status`, `source_evidence`, `finding_ids`
- `grounded_context_profiles[]`: `grounded_profile_id`, `segment_id`,
  `persona_archetype_id`, `context_stratum_id`, `profile_snapshot`,
  `context_attribute_provenance`
- `profile_snapshot`: `role_context`, `decision_context`, `motivations`,
  `anxieties`, `proof_needs`
- provenance rows: `attribute`, `value`, `status`, `source_evidence`,
  `finding_ids`
- `replicate_strategy`: `worker_unit`,
  `shared_context_fallback_allowed`, `fields_allowed_to_vary`,
  `fields_never_to_invent`
- `calibration_history[]`: `date`, `source_type`, `mapped_run_id`,
  `mapped_variants`, `mapped_segments`, `objective`, `time_window`,
  `data_quality`, `directional_alignment`, `action`, `what_was_learned`,
  `next_run_guidance`
- `refresh_conditions`: `review_after`, `max_age_days`, `triggers`
- `governance`: `pii_policy`, `allowed_uses`, `excluded_uses`,
  `privacy_confirmation`

`scope_fingerprint` is computed by the deterministic implementation. Do not
hand-author it.

## Evidence And Combination Rules

- Every research-backed segment resolves to an approved segment hypothesis.
- Archetype traits retain finding and evidence provenance.
- Every context dimension and profile variation is `observed`, `estimated`, or
  `experimental`.
- Do not create an implicit archetype-by-stratum cross-product.
- Weights require named provenance; unsupported composition uses
  `planning_allocation`.
- Raw person-level and sensitive source data are prohibited. Evidence-grounded
  synthetic sensitive-audience concepts are permitted; unsupported stereotypes
  are not.
- Provisional packages cannot be registered or reused.

Run the deterministic shared validators before packaging. A validation failure
is a stop, not permission to invent support.

## Package

`audience-panel-package-v2` binds:

```text
persona-research-brief.json
saved-audience-panel.json
research-sources.csv
audience-research-report.html
README.txt
package-manifest.json
audience-panel-package.zip
```

The build directory may additionally contain the source plan, finding-support
audit, synthesis matrix, panel summary, validation report, refresh diff, or
audit report. These readable audit files do not change the immutable v2 ZIP
allowlist.

Register only an approved research-backed package. Registration is immutable;
changed content requires a new semantic version.
