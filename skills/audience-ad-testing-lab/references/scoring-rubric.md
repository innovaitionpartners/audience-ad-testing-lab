# Method, Scoring, And Validity Contract

Use this reference to explain the two study methods, the finalist 1-5 rubric, deterministic screening outputs, boundary resolution, and validity limits. Workers collect structured responses; CLIs own every aggregate calculation.

## Method Boundary

### `complete_exposure`

- Use complete exposure for 2-6 creatives. New v3 runs use a frozen profile-aware core plus balanced reserve blocks; frozen v2 runs retain nine counterbalanced whole-set jobs per reported segment.
- Every first-round synthetic replicate sees every creative through progressive reveal and returns an exact complete-set ranking after all reactions are frozen.
- The deterministic aggregator converts ranks to normalized within-set scores and centers utility. New profile-aware v3 runs use exactly 2,000 seeded whole-record bootstrap resamples within grounded profile with duplicate adjustment, then combine frozen profile weights within frozen segment weights. Frozen v2 runs retain their segment-stratified bootstrap interpretation.
- `complete_exposure` never uses four-item MaxDiff blocks.
- Any complete-set signal is conditional on the complete set and its recorded protocol.

Do not describe a complete-exposure study with MaxDiff, subset, boundary-candidate, or Davidson language. If a person makes an override, label it in the roster decision rather than presenting it as a model result.

### `partial_exposure_maxdiff`

- Use for 7-100 creatives after the burden pilot passes.
- Deterministic assignments contain four creatives, balance exposure and position, and require connected, one-block-resilient comparison graphs.
- Each accepted `screening_response` contributes one usable best-worst block only when all four progressive reactions are judged and the choice is valid.
- The MaxDiff estimator and bootstrap run only through `python3 scripts/aggregate-screening.py screening`.
- A separate boundary stage is allowed only for the frozen `boundary_candidate` group and only through the predeclared reserve.

## Finalist 1-5 Rubric

Every approved finalist is scored on the same whole-number scale:

| Score | Meaning |
|---:|---|
| 1 | Poor fit, unclear, or active rejection. |
| 2 | Weak and needs material revision. |
| 3 | Mixed or acceptable. |
| 4 | Strong with limited caveats. |
| 5 | Unusually strong for the supplied profile and decision context. |

Required fields:

| Field | Question |
|---|---|
| `comprehension` | Is the offer and point understood? |
| `relevance` | Does it connect to the supplied role, problem, or buying stage? |
| `credibility` | Are the claim, proof, and tone believable? |
| `offer_appeal` | Is the value exchange attractive? |
| `motivation` | Does it create interest in a next step? |
| `friction` | How much confusion, skepticism, effort, or objection appears? Higher means more friction. |
| `attention_potential` | Does the original creative appear likely to earn notice in the supplied media context? |
| `overall` | What is the whole-ad assessment after the component judgments? |

The worker returns the eight scores; it does not create weighted totals. If input fidelity prevents a judgment, return inability/caveat data rather than inventing a rating.

Attention heatmaps never change rubric scores. They are revealed only after the deterministically proposed roster is approved.

## Finalist First Choice

`final_preference_ranking` is an exact permutation of the approved finalist IDs. Deterministic compilation counts each accepted finalist response once and reports:

- `accepted_response_records`
- `accepted_unique_replicates`
- `unique_job_slots_consumed`
- `total_model_calls`
- `first_choice_counts`
- `conditional_first_choice_share`

These shares are conditional only on the approved finalist set. They are not incidence, reach, survey share, or evidence about a human population.

## MaxDiff Estimand

The first-round large-library estimand is `centered_protocol_relative_log_utility` from a weighted joint best-worst MaxDiff model.

- Utilities sum to zero for identification.
- They are protocol-relative and comparable only within the current run.
- For v3 profile-aware runs, overall analysis uses locked profile weights over each profile's realized usable share, reconciled exactly to the locked segment weights. Older manifests without a profile-weight envelope retain locked segment weighting over each segment's realized usable share.
- Regularization supports estimation; it cannot repair a disconnected comparison graph.
- No prompt computes, edits, or ranks utilities.

## Coverage And Design Gates

For new v3 complete-exposure runs, planning and validity floors are enforced by grounded profile and frozen weight, not inferred from segment count. The provisional floor is experimental pending repeatability calibration. Frozen v2 and partial-MaxDiff policies retain their version-bound participation floors; none is universal human-sample adequacy.

The closed screening run checks:

- overall and per-reported-segment connectedness;
- for a v3 profile-aware run, connectedness after removing any one grounded profile and planned/usable creative participation floors inside every profile;
- model identification and convergence;
- usable-participation coverage;
- planned-participation coverage;
- overall and per-segment one-block deletion resilience;
- bootstrap fit success;
- leave-one-persona-archetype-out sensitivity;
- versioned library, shortlist, segment, tie/inability, and utility-separation bands;
- exact match between the manifest model and recovery configuration.

For profile-aware complete exposure, the versioned policy requires grounded-profile and archetype sensitivity when the locked scope contains multiple such units, plus per-profile usable floors, frozen profile-then-segment weighting, and duplicate-adjusted profile-stratified bootstrap. An unevaluable exclusion cannot pass. In an honestly single-profile provisional scope, both leave-one-out checks are explicitly not applicable and the result is labeled conditional on that one modeled context; do not invent extra profiles merely to satisfy a diagnostic.

Segment utilities or segment decisions require their own coverage, connectedness, convergence, and stability. An overall pass never grants an underpowered segment a pass.

## Conditional Within-Run Stability

The bootstrap resamples whole accepted synthetic replicate records. Profile-aware v3 runs stratify within locked grounded profile; older manifests stratify within locked segment. Production settings are:

- exactly 2,000 requested fits;
- `0.95` successful-fit floor;
- resample unit `whole_synthetic_replicate_record`;
- stratification `locked_grounded_profile` for profile-aware v3, otherwise `locked_segment`.

The diagnostic `conditional_within_run_top_k_inclusion_frequency` says how often a creative returned to this run’s top K under that resampling design. It is not a standard error, a confidence interval, or population uncertainty.

Inclusive product thresholds apply only when the run is `valid`:

- frequency `>= 0.90`: `clear_finalist`
- frequency `<= 0.10`: `clear_non_finalist`
- strictly between: `boundary_candidate`

When the recovery configuration is not calibrated or another required gate fails, classifications remain `unresolved` even if raw frequencies exist.

## Recovery Configuration

`references/screening-recovery-config.json` uses a strict key allowlist and records:

- version and `calibration_status`;
- supported library-size and shortlist-size bands;
- supported segment-count band;
- combined tie/inability-rate band;
- utility-separation band;
- planned and usable participation floors;
- bootstrap count and successful-fit floor;
- shortlist thresholds.

The public configuration is `screening-recovery-v0-unvalidated` with `calibration_status: exploratory_only`. It forces `exploratory`; it cannot produce `valid` until the declared recovery studies support a calibrated configuration.

## Validity Precedence

Apply states in this order:

1. Incomplete takes precedence while collection is open.
2. Disconnected or unidentified models are invalid. Nonconvergence of an identified model is also invalid.
3. A closed, identified run is `valid` only when the recovery configuration is calibrated and every required gate passes.
4. Other closed, usable identified runs are `exploratory` with explicit reasons.

The four screening states are `valid`, `exploratory`, `invalid`, and `incomplete`. Do not promote a state in prose.

## Separate Davidson Boundary Model

The MaxDiff classifications freeze `clear_finalist`, `clear_non_finalist`, and `boundary_candidate` groups. Only boundary candidates enter the pairwise stage.

Run the separate estimator with:

```bash
python3 scripts/aggregate-screening.py boundary \
  --manifest study-manifest.json \
  --screening-results screening-model-results.json \
  --responses boundary-responses.jsonl \
  --output boundary-results.json
```

The boundary estimand is `centered_pairwise_davidson_log_utility`; its stability field is `conditional_within_run_boundary_slot_inclusion_frequency`. Its scale is boundary-only and never pooled with MaxDiff utility.

Boundary classifications use the same inclusive `0.90`/`0.10` product rule. Cutoff-tied inclusion is allocated symmetrically; creative ID can serialize display order but can never select a winner.

Boundary status is `resolved`, `unresolved`, or `invalid`. `unresolved` is an honest refusal state when the rule is not met before the authorized plan ends.

## Binding Reserves

These reserves are unique synthetic-replicate/job slots. `boundary_reserved = boundary_jobs_per_wave * boundary_waves_max`. The frozen boundary plan may instantiate only its predeclared boundary jobs. A retry or rejected attempt does not consume another slot; it remains inside the same job and increases `total_model_calls`.

`finalist_reserved` remains unchanged through screening and boundary work. A boundary estimator never borrows finalist slots. New job dispatches after an inclusion stop, later-wave jobs before the current wave completes, or jobs beyond reserve are invalid protocol events.

## Reporting Rules

- Show exact method ID, estimand, stability field, thresholds, recovery version, validity state/reasons, and run-specific limits in Methodology/Test details.
- Show the exact accepted base beside every model-call proportion.
- Keep total calls, accepted records, accepted unique replicates, archetypes, and grounded profiles separate.
- Preserve ties, inability records, rejected attempts, and dissent.
- Make no population inference.
- Never describe internal stability as human alignment or campaign validity.
- Never let a prompt own aggregate math.
