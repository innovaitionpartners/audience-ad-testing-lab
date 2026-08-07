# Review And Response Contracts

Use this reference to build `synthetic_replicate_jobs`, enforce progressive exposure, validate raw returns, and assemble discriminated response records.

## Separation Of Responsibilities

The orchestrator owns IDs, assignment truth, reveal order, retry history, raw-return retention, source provenance, and schema validation. A worker owns only the requested reaction or comparison fragment. Deterministic CLIs own utilities, shares, stability, validity, and boundary decisions.

Use one context-isolated synthetic replicate per worker where available. Every response must preserve `human_sample_independence: false`.

## Enriched Job Shape

```yaml
study_id: ""
response_id: ""
record_type: screening_response | boundary_response | finalist_response
method: complete_exposure | partial_exposure_maxdiff
synthetic_replicate_id: ""
# v3 authority trio; all three are required together and copied unchanged
audience_slot_id: ""
grounded_profile_id: ""
profile_snapshot_sha256: "sha256:..."
dispatch_id: ""
persona_archetype_id: ""
segment_id: ""
context_stratum_id: "" # required when the frozen plan supplies one
profile_snapshot: {}
context_attribute_provenance:
  - attribute: ""
    value: ""
    status: observed | estimated | experimental
    source_evidence: []
worker_context_isolation: isolated | shared_context_fallback
human_sample_independence: false
variation_ids: []
blind_labels: {}
shown_order: []
reaction_protocol: progressive_reveal | reflective_reaction_caveat
reaction_prompts: []
comparison_prompt: ""
```

Cardinality is exact:

- `screening_response`: 2-6 assigned variations for complete exposure; exactly four for partial exposure.
- `boundary_response`: exactly two assigned variations from one authorized, predeclared partial-exposure pair.
- `finalist_response`: two to six variations, exactly the approved finalist set.

`shown_order` is an exact permutation of `variation_ids`. Blind-label keys match the same set and values are unique. Obsolete one-shot `prompt`, `panelist_jobs`, `reviews`, and `panelist_reviews` shapes are invalid.

Validate jobs before dispatch:

```bash
python3 scripts/validate-panel-run.py jobs.json \
  --manifest study-manifest.json \
  --audience-resolution audience/resolution.json \
  --dispatch-authority stage-dispatch-authority.json \
  --expected-count <N>
```

The three v3 authority options are mandatory for v3. Legacy v2 requires the
separate producer output:

```bash
python3 scripts/prepare-panel-jobs.py \
  assignment-core.json dispatch-context.json jobs.json \
  --audience-resolution audience/resolution.json \
  --legacy-v2-origin-authority-output legacy-v2-origin-authority.json
python3 scripts/validate-panel-run.py jobs.json \
  --legacy-v2-origin-authority legacy-v2-origin-authority.json \
  --expected-count <N>
```

The producer also writes a read-only
`legacy-v2-origin-authority.evidence/` directory. Preserve the record and that
directory together. Pass the record's canonical absolute path to the workflow
as `legacy_v2_origin_authority`; do not pass a parsed authority object. The
workflow reopens the bound evidence. This path cannot be combined with an
authenticated v3 envelope.
Use the complete frozen study plan as screening authority, the screening
result containing the bound `boundary_plan` as boundary authority, and the
approved finalist-roster decision as finalist authority.

## Progressive Reveal

An `immediate` reaction is valid only when orchestration reveals exactly one creative per call under `progressive_reveal`.

For each replicate:

1. Reveal the next creative only.
2. Collect and validate the reaction.
3. Retain the raw provider return and its validation outcome.
4. Retry a malformed reaction once without revealing any additional creative.
5. Freeze the accepted reaction ID and verbatim text.
6. Continue to the next assigned creative.
7. Assemble the comparison prompt only after every assigned reaction is accepted and frozen.

If the runtime cannot enforce single-creative calls, use `reflective_reaction_caveat` and `reaction_label: reflective`. Never relabel a reflective return as immediate.

## Shared Response Envelope

Every accepted record contains:

```yaml
study_id: ""
response_id: ""
record_type: ""
method: complete_exposure | partial_exposure_maxdiff
synthetic_replicate_id: ""
# v3 authority trio; copied exactly from the authorized job
audience_slot_id: ""
grounded_profile_id: ""
profile_snapshot_sha256: "sha256:..."
reviewer_dispatch_id: ""
persona_archetype_id: ""
segment_id: ""
context_stratum_id: "" # required when the frozen job supplies one
profile_snapshot: {}
context_attribute_provenance: []
worker_context_isolation: isolated | shared_context_fallback
human_sample_independence: false
assigned_variation_ids: []
blind_labels: {}
shown_order: []
reaction_protocol: progressive_reveal | reflective_reaction_caveat
runtime_attempts:
  - attempt_id: ""
    stage: reaction | comparison
    position_seen: 1
    attempt_number: 1
    provider_return_id: ""
    outcome: accepted | rejected
    validation_errors: []
validation:
  schema_valid: true
  assignment_valid: true
  reaction_order_valid: true
```

V2 enriched jobs may retain `grounded_profile_id` alone. A v3 job is marked by
`audience_slot_id` and `profile_snapshot_sha256`; both markers require the
complete three-field authority trio. The workflow copies that trio unchanged
into the v3 response. Partial presence or recalculation is invalid.

Retain accepted and rejected raw provider returns outside the accepted envelope as `raw_provider_returns` and `rejected_attempts`, including the exact raw payload, attempt number, errors, and disposition. Accepted derived fields cite the accepted `provider_return_id` with `capture: verbatim_provider_return`.

## Attempt Lineage And Slot Accounting

Accepted and rejected attempt lineage has two canonical delivery names:

- `raw-provider-returns.jsonl`: one row per provider/model call, including the exact return and its attempt identity.
- `rejected-attempts.jsonl`: one row per rejected attempt, linked to the raw return and preserving nonempty `validation_errors`.
- `dispatch-audit.jsonl`: one row per dispatched job slot, including stage, replicate, dispatch, accepted/exhausted outcome, exact `reaction_attempts` and `comparison_attempts` call counts, and an `attempt_contract` with the manifest-bound retry limit, authorized reaction positions, and comparison requirement.

The accepted response keeps its full `runtime_attempts` history and accepted source provenance. A retry is allowed only after a schema-invalid return, never after a surprising opinion. Attempt identity is dispatch + replicate + stage + reaction position + attempt number; changing the provider-return ID cannot make a duplicate attempt legal. Raw calls must exactly match the audit's authorized reaction and comparison attempt counts, with no missing or extra calls. If one concurrent reaction exhausts the exact rejected sequence `1..retry_limit_per_return + 1`, retain valid accepted component calls from the other authorized positions and do not call comparison. If all reactions succeed but comparison exhausts that exact sequence, retain both comparison attempts. Either outcome leaves the dispatch incomplete with zero accepted composite responses.

First-round partial-exposure aggregation also binds accepted records to the frozen jobs. The job set must be unique and match the manifest's study, method, stage, planned slot count, audience lock, creative roster, and assignment block size. Each accepted response must be an exact job match; missing planned jobs require an audit row proving terminal exhaustion. An internally self-consistent response is still invalid when its replicate or dispatch was never planned.

Progressive-reveal calls, retries, and rejected attempts increase `total_model_calls`. They do not consume another unique synthetic-replicate/job slot because they remain attempts inside the same predeclared job. Run `scripts/materialize-run-lineage.py` to write all four canonical files, bind their paths, hashes, and record counts under manifest `outputs`, and make them available to dashboard Downloads.

## Per-Creative Reaction

Screening and boundary records use `per_creative_reactions`; finalist records use `finalist_reviews`. Each reaction preserves:

```yaml
reaction_id: ""
variation_id: ""
display_label_seen: ""
position_seen: 1
reaction_label: immediate | reflective
immediate_reaction: ""
noticed_or_understood_first: ""
strongest_positive_signal: ""
strongest_negative_signal: ""
judgment_status: judged | unable_to_judge
source_provenance:
  provider_return_id: ""
  capture: verbatim_provider_return
```

The variation, label, position, and order must match the job. An inability to judge is data; do not invent missing image, proof, landing, or platform context to avoid it.

## `screening_response`

For `partial_exposure_maxdiff`:

```yaml
record_type: screening_response
per_creative_reactions: []
comparative_choice:
  status: best_worst | no_meaningful_difference | unable_to_judge
  best_variation_id: ""
  weakest_variation_id: ""
  best_reason: ""
  weakest_reason: ""
  frozen_reaction_ids: []
  source_provenance: {}
usable_maxdiff_block: true
```

`usable_maxdiff_block` is true only when all four reactions are valid and judged and `best_worst` names two different assigned variations. Ties and inability records remain retained but are not usable MaxDiff blocks.

This record contains no pairwise or finalist fields and no aggregate calculation.

For `complete_exposure`:

```yaml
record_type: screening_response
method: complete_exposure
per_creative_reactions: []
complete_set_evaluation:
  status: ranked | unable_to_judge
  preference_ranking: []
  frozen_reaction_ids: []
  source_provenance: {}
usable_complete_exposure_observation: true
```

The ranking is an exact permutation of all 2-6 assigned variations and becomes usable only after every progressive reaction is valid and judged. This record contains no MaxDiff best/weakest field, pairwise field, or finalist field.

## `boundary_response`

```yaml
record_type: boundary_response
per_creative_reactions: []
pairwise_choice:
  status: first_preferred | second_preferred | tie | unable_to_judge
  preferred_variation_id: ""
  reason: ""
  frozen_reaction_ids: []
  source_provenance: {}
usable_pairwise_observation: true
```

The preferred variation must match the first or second item in `shown_order`. A tie has no preferred ID and is usable by the Davidson model. An inability record has no preferred ID and is not usable. This record contains no MaxDiff best/weakest or finalist fields.

## `finalist_response`

After roster approval, every fresh finalist replicate sees the complete approved set through progressive reveal. Its comparison return contains:

```yaml
record_type: finalist_response
finalist_reviews:
  - reaction_id: ""
    variation_id: ""
    rubric_scores:
      comprehension: 1
      relevance: 1
      credibility: 1
      offer_appeal: 1
      motivation: 1
      friction: 1
      attention_potential: 1
      overall: 1
    feedback: []
    rubric_source_provenance: {}
final_preference_ranking: []
```

Every rubric score is a whole number from 1-5. `final_preference_ranking` is an exact permutation of the approved finalist IDs. A finalist response contains no first-round or boundary choice fields.

Heatmaps are never shown during reaction or comparison collection. They are generated or imported after the deterministically proposed roster is approved.

## Validation

Validate responses against their exact jobs:

```bash
python3 scripts/validate-panel-run.py jobs.json \
  --manifest study-manifest.json \
  --audience-resolution audience/resolution.json \
  --dispatch-authority stage-dispatch-authority.json \
  --responses responses.jsonl --expected-count <N>
```

A run is incomplete when required accepted records are missing. Do not synthesize a complete result from partial returns unless the user explicitly authorizes a separately labeled incomplete analysis; even then, deterministic validity remains `incomplete` while collection is open.

## Prompt Discipline

- Collect reactions, reasons, choices, 1-5 finalist rubrics, and exact rankings only.
- Do not expose aggregate results or expected winners to a worker.
- Do not coordinate diversity across replicates.
- Do not ask a worker to calculate utility, shares, bootstrap stability, segment weights, or shortlist membership.
- Do not retry an unexpected opinion.
- Do not let a prompt alter assignments, reserves, method, validity, or approval state.
- Do not use attention heatmaps as a reaction input or score modifier.
