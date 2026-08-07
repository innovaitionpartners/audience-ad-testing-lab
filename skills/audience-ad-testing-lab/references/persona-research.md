# Ad Testing Lab Persona Research

Use this reference before creating or materially updating a reusable audience panel. Research produces an evidence record and segment hypotheses. It does not produce final personas or authorize its own use.

## Default Research Basis

When the user supplies a target audience without a saved panel, research is required. Choose the mode from the evidence actually available:

- `public_research`: default for an audience-only request.
- `user_provided_research`: supplied documents, studies, or de-identified summaries are the evidence base.
- `crm_first_party`: permissioned, aggregated, anonymized first-party evidence.
- `hybrid_research`: a documented combination of those sources.
- `use_existing_saved_panel`: resolve an already approved immutable package; do not recreate its research.
- `provisional_no_research`: explicit, lower-confidence exception accepted by the user for one immediate run. Never label it research-backed.

Use `quick_directional`, `standard`, or `robust` research depth based on decision risk. Source-count ranges are planning aids, not a substitute for direct relevance and coverage.

## Research Brief Contract

The researcher returns JSON conforming exactly to `audience-research-brief-v2`. Unknown keys fail validation.

Top-level keys:

```text
schema_version, brief_id, created_at, updated_at, status, target_audience,
research_mode, research_depth, research_questions, evidence_sources, findings,
coverage, segment_hypotheses, evidence_gaps, privacy_confirmation, approval
```

Nested keys:

- `target_audience`: `audience`, `category`, `market`, `geography`, `buying_context`, `exclusions`
- `evidence_sources[]`: `evidence_id`, `type`, `source_label`, `source_url`, `collection_method`, `date`, `confidence`, `usable_for`, `permitted_uses`, `limits`
- `findings[]`: `finding_id`, `evidence_ids`, `statement`, `category`, `confidence`, `inference_boundary`, `creative_implications`
- `coverage`: exactly `pain_points_challenges`, `motivations_goals`, `decision_criteria`, `buying_triggers`, `fears_objections`, `proof_needs`, `media_behaviors`; each is `strong`, `thin`, or `empty`
- `segment_hypotheses[]`: `segment_id`, `name`, `origin`, `finding_ids`, `evidence_ids`, `confidence`, `why_it_matters_for_ad_testing`
- `evidence_gaps[]`: `gap`, `impact_on_panel`, `mitigation`
- `privacy_confirmation`: `confirmed`, `confirmed_by`, `confirmed_at`, `note`
- `approval`: `approved_for_panel_creation`, `approved_by`, `approved_at`, `approval_note`

Research-backed segment origins are `research_derived` and `user_proposed_research_validated`. `provisional_user_defined` is allowed only for the explicit provisional route and carries no findings or evidence IDs.

## Research Workflow

1. Lock audience, category, market, geography, buying context, exclusions, research mode, depth, and the ad decision.
2. Inventory supplied evidence and assign stable evidence IDs before extracting findings.
3. Define questions covering pain points, motivations, decision criteria, triggers, objections, proof needs, and media behavior.
4. Gather only verifiable public evidence and permissioned aggregate first-party evidence. Never invent sources, URLs, dates, statistics, or patterns.
5. Give each finding evidence IDs, a confidence label, an inference boundary, and creative implications.
6. Mark every coverage category `strong`, `thin`, or `empty`; record thin or empty decision-relevant areas as explicit gaps.
7. Propose evidence-backed segment hypotheses. Do not create persona archetypes, context strata, or grounded profiles.
8. Confirm that the brief contains no raw individual data and does not operationalize sensitive traits.
9. In `approval_gate` mode, store and present the complete draft-shaped candidate field-for-field. A summary is not the approval authority. The final validator intentionally does not accept a draft as reusable authority.
10. After user approval, deep-copy that exact stored candidate. The only permitted mutations are `status`, `updated_at`, and `approval`: set approved status, emission time, and the supplied approval record. Canonical-JSON compare every other field unchanged, then validate with `validate_research_brief` before panel construction.

In Codex, public-research collection may use a small bounded set of research subagents with separate source lanes. The parent reconciles and validates all evidence. This is an orchestration option, not a change to the private runtime package's Claude Code platform scope.

## Evidence Discipline

Each finding must resolve to existing evidence. Segment hypotheses must cite both findings and evidence. If support is absent, record a gap rather than inferring a plausible trait.

Private first-party evidence must be aggregated and anonymized. Never store names, email addresses, phone numbers, street addresses, account or contact IDs, speaker identities, raw CRM rows, or raw transcript identities. Aggregate research may discuss sensitive subjects when relevant, but race or ethnicity, religion, sexual orientation, health or disability, biometrics or genetics, exact geolocation, financial-account data, politics, union membership, and citizenship or immigration status must not become operational audience attributes.

## Approval Gate

Approval is a user decision. The researcher cannot self-approve. Before a canonical brief file exists, present:

- audience and research mode;
- source types and evidence IDs;
- major findings and inference boundaries;
- coverage by category;
- segment hypotheses with origins and evidence;
- gaps and their effect on the proposed panel;
- privacy confirmation state.

After approval, the researcher or orchestrator deep-copies the stored candidate. It may change only `status`, `updated_at`, and `approval`; `approval` must be copied from the user-supplied approval record. `schema_version`, `brief_id`, `created_at`, `target_audience`, `research_mode`, `research_depth`, `research_questions`, `evidence_sources`, `findings`, `coverage`, `segment_hypotheses`, `evidence_gaps`, and `privacy_confirmation` remain byte-equivalent under canonical JSON. Panel construction begins only after that exact approved JSON passes. Any other delta returns to this gate for review and approval.

The provisional exception requires explicit acceptance, an expiry no more than 30 days after acceptance, empty research sources and findings, empty coverage, low-confidence user-defined segments, and `source_state: no_research_sources`. It is packaged for the immediate run only, never registered or reused, and must be refreshed through research before registration.

## Validation Checklist

- The mode reflects the actual evidence base; public research is the audience-only default.
- Evidence IDs, finding IDs, and segment IDs are canonical and unique.
- Findings resolve to sources; segment hypotheses resolve to findings and evidence.
- Segment origins are exact and honest.
- Coverage, evidence gaps, privacy confirmation, and approval are explicit.
- The user, not a worker, approves panel construction.
- Provisional work is visibly lower confidence and cannot enter the reusable library.
