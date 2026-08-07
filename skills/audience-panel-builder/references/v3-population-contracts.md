# V3 Population Contracts

These contracts separate population structure, audience-bound overlays,
effective reusable profiles, validity evidence, and later outcome feedback.
Every object is closed: unknown or missing fields fail validation at every
nested level.

## Release B1 route and output boundary

Population-aware construction uses one of two evidence paths:

- `create_research_backed_panel` may normalize supported public sources
  directly or consume an already approved aggregate handoff.
- `import_authorized_audience` never reads arbitrary authorized files in
  Panel Builder. Audience Data Lab must first profile privacy, obtain exact
  mapping approval, transform deterministically, report every loss, and emit a
  validated `authorized-audience-handoff-v1`.

The other four workflow routes remain `refresh_existing_panel`,
`augment_existing_panel`, `audit_existing_panel`, and
`provisional_immediate_panel`. V2-to-v3 migration is an explicit maintenance
command, not a seventh route. The intake validator enforces this closed
six-route enum. The source planner executes `import_authorized_audience` as
one unresolved first-party handoff requirement with no selected source
family; it never opens the supplied authorized files.

Release B1 ends at validated v3 documents: population-frame result,
composition plan, validity profile, research brief, and saved panel. It may
also emit a read-only outcome-feedback binding or a non-executable calibration
proposal. It does not define a v3 package or library operation, a study quota
or job, a profile assignment, a test result, a score or rank, a dashboard, or
evaluation behavior.

Conformance checks enforce that boundary recursively across mapping and list
key paths, including singular/plural and hyphen/underscore spellings. V3
package manifests or archives, `.zip` outputs, and resolver envelopes or
outputs are Release B2 structures and fail the B1 boundary.

## Public and authorized population proofs

Public adapters preserve source universes rather than inventing one universal
audience method. BLS OEWS person estimates, Census SUSB employer-firm
estimates, and Census CBP establishment estimates occupy separate
unit/denominator partitions. They cannot be summed or reconciled together.
Public frames retain proxy language, observed and modeled status, explicit
missing or suppressed cells, and every unavailable required joint. Missing a
critical joint or calculating modeled share above `0.30` forces an
experimental result and Tier 1 composition with exact downgrade reason codes.
A compatible complete public frame may be eligible for Tier 2, but never
gains a universal-market claim merely because multiple public adapters ran.

Authorized frames bind the exact handoff and cohort denominator. Equivalent
flat CSV, wide XLSX, nested JSON, and linked-file sources must converge to
byte-identical canonical frame, structured-evidence, social-observation, and
outcome-feedback documents; only source-profile and transformation provenance
may differ. Structural distributions stay separate from qualitative
psychographic/affinity summaries and topic-bound social evidence. Aggregate
outcomes retain the exact metric identity and attribution window and remain
read-only. A complete authorized frame may be eligible for Tier 3. A
calibration factor of exactly `3.0` is valid; `3.0000001` fails at the request
boundary.

Social integration is ID- and cohort-bound. Every transformed
`social-evidence-*` observation used by an overlay appears in that overlay's
evidence support and in at least one supported composition profile. That
profile's structural group resolves to the exact canonical cohort frame cell.
Topic labels alone do not satisfy this binding.

Use the validators exported by `audience_lab`:

```python
validate_frame_request(payload)
validate_observation_batch(payload)
validate_population_frame(payload)
validate_composition_plan(payload, frame=frame_result)
validate_research_brief_v3(payload, now=None)
validate_saved_panel_v3(payload, now=None)
validate_outcome_feedback(payload)
validate_validity_profile(payload)
validate_audience_research_v3(
    brief,
    panel,
    frame=frame_result,
    composition=composition,
    validity=validity,
    workflow_state=workflow_state,
    construction_audit=construction_audit,
)
```

All numeric inputs must be finite JSON numbers. Validators return new
canonical-shaped dictionaries and never mutate their inputs.

Package consumers use
`audience_package.read_validated_package_archive(source)` when they need the
validated contents of an existing v2 package. It consumes the source once,
validates that exact byte snapshot with the unchanged v2 archive validator,
and returns the exact archive bytes, the canonical validation result, and the
allowlisted member bytes. Callers do not use private archive helpers.

## Independent classification axes

Panel tier and evidence basis are separate enums. A tier never implies an
evidence basis, and an evidence basis never upgrades a tier.

Panel tiers:

- `tier_1`
- `tier_2`
- `tier_3`
- `tier_4`

Evidence bases:

- `public`
- `licensed_aggregate`
- `first_party_aggregate`
- `hybrid`
- `none`

Tier 1 may result from an experimental or `no_defensible_frame` result, in
which case it has no usable population-frame binding. It may also result from
an eligible frame when a used experimental overlay forces a composition
downgrade and the evidence basis is not `none`; that route preserves the
eligible frame and its exact usable-frame binding. `evidence_basis: none` is
the conservative exception to otherwise independent classification: it
requires Tier 1 and a null usable-frame binding. Tier 2 requires a frame
eligible for Tier 2 or Tier 3. Tier 3
requires `eligible_tier_3`, an exact authorized aggregate handoff, an exact
cohort denominator, selection and coverage statements, and calibration
factors no greater than 3. Tier 4 requires an eligible frame, a predeclared
validation design, held-out outcome evidence, and `held_out_validated`
external-validity status; it does not inherit the Tier 3 authorization rule.

## Tier 4 preregistration and validation evidence (Release C1)

Release C1 keeps Tier 4 evidence outside existing v2 and v3 panel packages.
It adds seven independently self-hashed, closed documents:

- `panel-validation-preregistration-v1`: exact panel/package bytes, frozen
  synthetic surface and result, complete claim scope, metrics, block-level
  holdout partition, prior-outcome access, analysis and multiplicity rules,
  and design approval. Its status is `registered` or `withdrawn`; only a
  sealed `registered` document can proceed. Multiplicity rules preregister the
  family ID, alpha, correction method, and complete ordered membership.
- `panel-shared-outcome-evidence-v1`: aggregate-only revealed outcome
  evidence. It includes study/block/arm/creative identity, the exact outcome
  scope, metric, aggregate sufficient statistics, design, source permission,
  windows, exclusions, and limitations. It must not contain registration,
  panel, package, synthetic run/result, evaluation, or claim bindings.
- `panel-validation-observation-v1`: the panel-specific audit record. It
  repeats the outcome fields and must reproduce the exact shared-evidence
  hash, while separately binding its registration, panel, package, frozen
  synthetic result, and complete claim scope.
- `panel-synthetic-outcome-comparison-v1`,
  `panel-held-out-evaluation-v1`, `panel-tier4-claim-v1`, and
  `panel-validation-claim-family-v1`: respectively the exact arm mapping,
  deterministic evaluation, narrowly scoped lifecycle claim, and complete
  preregistered multiplicity family.

Every self-hash is calculated with its own digest field set to `null`. All
validators return fresh canonical copies; unknown versions, unknown nested
keys, duplicate IDs, nonfinite numbers, and person-level fields fail.

### Sealed synthetic surface and compact downstream binding

The preregistration's `synthetic_surface` is the one full, closed producer
identity. It contains exactly:

```text
surface
method
stage
run_id
result_path
result_sha256
result_bytes_sha256
manifest_sha256
lineage_bundle_sha256
producer_evidence_sha256
producer_semantics_sha256
frozen_at
producer_evidence_sealed_at
eligible_creatives
```

Surface identity is fixed: complete exposure is
`method=complete_exposure`, `stage=screening`, and
`result_path=screening-model-results.json`; MaxDiff screening is
`method=partial_exposure_maxdiff`, `stage=screening`, and the same result
path; pairwise boundary is `method=partial_exposure_maxdiff`,
`stage=boundary`, and `result_path=boundary-results.json`.

`result_sha256` and `manifest_sha256` are canonical-document digests.
`result_bytes_sha256` is the raw frozen result-file digest.
`lineage_bundle_sha256` is the canonical digest of the ordered four-file
lineage binding list. `producer_evidence_sha256` identifies the sealed
producer-evidence record, and `producer_semantics_sha256` identifies its
complete closed producer semantics. `producer_evidence_sealed_at` is the
evidence record's exact `sealed_at` value.

`eligible_creatives` is a non-empty, strictly `creative_id`-sorted list of
unique `{creative_id, creative_sha256}` entries copied from the manifest's
creative asset hashes. It is part of the preregistration surface, not an
unbound comparison roster.

Only this derived compact binding may appear in claim scope, observations,
comparisons, evaluations, and claims:

```python
{
    "surface": synthetic_surface["surface"],
    "run_id": synthetic_surface["run_id"],
    "result_sha256": synthetic_surface["result_sha256"],
}
```

Validators derive this projection from the sealed surface; callers cannot
substitute a full surface or a separately supplied projection downstream.

### Producer-evidence receipt and durability

Before preregistration, the selected complete-exposure, MaxDiff-screening, or
pairwise-boundary result must have one
`panel-synthetic-producer-evidence-v1` receipt. The verifier authenticates the
final manifest and four cumulative lineage files, derives the physical-order
stage-response projection, seals the unchanged producer source and numerical
runtime, freezes those exact files in a canonical snapshot, and reproduces the
existing result in an isolated write-restricted subprocess. It does not
rescore, rerank, or replace the result.

The receipt is the flat immutable file
`<surface>--<run_id>--<result-sha256-without-prefix>.producer-evidence.json`
beneath an existing trusted evidence root. It contains exactly the receipt
version, surface/method/stage/run identity, snapshot freeze and evidence seal
timestamps, closed producer semantics, input and result bindings, the exact
snapshot binding, and its self-hash. Complete exposure and MaxDiff bind the
manifest, four lineage files, nullable command audit, jobs, derived screening
projection, and recovery configuration. Pairwise binds the manifest, lineage,
derived boundary projection, screening result, and recursively authenticated
screening producer evidence. The chosen result appears only in
`result_binding`.

Both the evidence root and snapshot root must be existing euid-owned real
directories that are not group- or world-writable. Receipt validation always
reopens and fsyncs the exact receipt file and root, revalidates its identity,
mode, length, digest, closed schema, self-hash, snapshot, complete archive
member world, runtime semantics, and optional revocation state, then jointly
recovers the snapshot archive and commit. Persistent-looking bytes alone do
not authorize a Tier 4 claim. A file or root fsync failure after complete
canonical bytes is an indeterminate publication until the explicit recovery
mode succeeds.

An optional revocation is the separate flat
`<surface>--<run_id>--<result-sha256-without-prefix>.revoked.json` marker. A
durably authenticated marker always denies claim use. Verification and
recovery never rename, link, remove, replace, quarantine, or automatically
clean a receipt, revocation, snapshot, or sibling.

`analysis_rules.tie_handling` is also closed by surface. Complete exposure
contains exactly `ordering_equivalence="exact-utility-equality-v1"` and
`ordering_tiebreak="creative-id-serialization-only-v1"`. MaxDiff screening
and pairwise boundary contain exactly
`ordering_equivalence="rounded-utility-bucket-v1"`,
`ordering_tiebreak="creative-id-serialization-only-v1"`, a finite positive
`effective_ordering_tolerance`, and
`rounding_rule="python-half-even-v1"`.

Preregistration sealing checks only:

```text
frozen_at <= producer_evidence_sealed_at <= registered_at
frozen_at < every prior_outcome_access.accessed_at
```

Held-out outcome chronology is checked when an observation is validated:

```text
frozen_at < outcome_accessed_at
registered_at < outcome_accessed_at
```

`outcome_scope` has exactly cohort/segment, channel, placement, objective,
geography, and validation-window identity. The outcome subset of a complete
observation or claim scope must equal it exactly. Panel/package/synthetic
identity remains outside that shared scope, so the same aggregate outcome
evidence can be compared against more than one frozen ordering without
changing its hash.

An observation's `holdout_status` is derived, never trusted: only a sealed,
registered, permissioned source may enter evaluation-grade observation
validation, and registration must precede outcome access. Registered
prior-access source hashes are in-sample; a leakage flag is leaked; and a
non-randomized assignment is mismatched. Only an exact registered,
permissioned, randomized, non-leaked, previously unaccessed observation is
`eligible_held_out`. The observation carries the
sealed preregistration and repeated registration fields; validators prove that
its holdout partition, claim scope, multiplicity rules, prior-access hashes,
identity, and self-hash match the sealed source. Its block must be in the
preregistered holdout set, its arm must be in that block's planned-arm list,
its shared-evidence study ID must equal that block's registered study ID, and
its complete claim scope must match exactly.

Every observation embedded in a claim-family or evaluation comparison must
derive as `eligible_held_out`. A comparison with any in-sample, leaked,
mismatched, or descriptive observation is excluded from analysis and makes
the family/evaluation invalid. Coverage, sample, statistics, and segment
diagnostics use only fully eligible held-out blocks.

### Evaluation-grade metric normalization

`normalize_observation` consumes only a canonical
`panel-validation-observation-v1` and re-proves its shared-outcome binding
before using the aggregate sufficient statistics. The observation metric must
match a sealed preregistration metric exactly; its exposure/outcome units and
measurement/attribution windows must agree with the metric declaration.

The supported families are `binary_proportion`, `continuous_mean`, and
`event_rate`. Binary proportions use eligible exposures and two-sided Wilson
score intervals. Event rates use positive exposure time and exact
Poisson/Garwood intervals. Continuous means require sample count, mean, and
standard deviation; a one-observation arm is marked limited, never certain.
For lower-is-better metrics, the reported point stays in its original unit,
while comparison direction is reversed only in the normalized comparison
space.

Pair classification compares direction-normalized arms. Binary and event-rate
difference bounds are Bonferroni-conservative from 97.5% arm intervals;
continuous bounds use a 95% Welch Student-t interval. A direction requires an
entire interval beyond the practical-equivalence margin, equivalence requires
the complete interval inside that margin, and all other cases are
indeterminate. Sparse or degenerate calculations remain explicitly limited
and cannot produce a determinate observed ordering.
Unavailable continuous uncertainty is represented as unavailable bounds and
no nominal confidence level, rather than a point-width confidence interval.
Pairs must use distinct arms from the same sealed registration, block, and
study context.

A claim-family document embeds the sealed preregistration for every member.
The validator requires those documents to be registered and requires each to
declare the exact same family ID, alpha, correction method, and complete
ordered membership. The post-outcome document cannot add, remove, reorder, or
substitute a family member.

### Held-out evaluation gates and C1 closed-schema interpretation

`build_claim_family` constructs the complete ordered family before evaluation.
For each member it recomputes the one-sided complete-block sign-permutation
p-value from the member's validated comparisons, then applies Holm in the
registered order. It never accepts a caller-supplied p-value. A member's one
`member_comparison_sha256` is the canonical hash of its block-ID-sorted list
of comparison self-hashes; this binds a multi-block member without adding a
new comparison field to the closed contract.

The family embeds `member_comparisons` for every preregistered member. The
evaluator validates those closed comparison documents, checks their ordered
set hashes against `member_comparison_sha256`, recomputes every sibling's
one-sided p-value, recomputes Holm across the exact current family, and gates
on the current member's exact adjusted value. Supplied raw and adjusted
p-values are derived evidence and must match recomputation exactly. There is
no Bonferroni substitution.

`evaluate_held_out_ordering` applies gates in this order: invalid
identity/binding/leakage evidence; descriptive-only evidence; insufficient
sample, coverage, power, or uncertainty; eligible negative statistical or
material-segment result; and finally supported Tier 4. It emits an evaluation
for every eligible positive, negative, or limited result. Only
`decision.status == "tier4_supported"` may be passed to
`issue_tier4_claim`, which creates a deliberately narrow active claim.
The evaluation embeds its exact preregistration, comparisons, and family.
Claim issuance derives every diagnostic status from numeric evidence, replays
the complete evaluator with the live design capability, and requires exact
document equality. A caller-rehashed status summary cannot issue.

Every preregistration in the family, including siblings used by Holm, is
reauthenticated against the live runtime-pinned authority registry at every
portable trust boundary. Package validation, library reads, report rendering,
and dashboard rendering accept only that non-serializable live authority
capability. Self-hashes prove byte integrity but cannot authorize a design.

Because C1 v1 is unreleased, its closed schema carries the evidence required
by every promotion gate. Preregistration binds `study_design_power`, the
planned effective sample for every block, and the canonical
`segment_inventory`. Each block also binds exact sorted segment membership
for every planned arm. Every decision-relevant preregistration field forms one
canonical design-evidence projection: identity, chronology, scope, metrics,
holdout, full analysis/tie/bootstrap rules, eligibility, segment rules,
multiplicity/Holm, interim looks, power, blocks, membership, and weights. A
closed authority-registry entry names that digest, approver, chronology, and
authority root/index. Each segment inventory's planned block set must equal
the blocks whose planned arms carry that segment; neither omission nor phantom
membership is permitted. The registry must pass HMAC-SHA-256 authentication
with an out-of-band secret of at least 32 bytes whose production registry ID
and secret fingerprint are runtime-pinned before a live process-local
capability can be minted. The secret file is owner-only, runtime-user-owned,
regular, non-symlink, and identity-stable while read. No default secret exists;
neither secret nor capability is serialized.
Shared evidence and observations bind exposure counts, missing-outcome
counts, achieved effective sample, and exact approved segment IDs.
Each comparison embeds the validated observations, an arm-ordered observation
hash list, a recomputable `block_evidence` projection, and exact
`segment_evidence` projections. Status-only block diagnostics are rejected.

The v1 holdout partition is block-, campaign-, or time-batch-based. Campaign
and time-batch IDs resolve to registered study IDs, and block partitioning may
not split blocks that share one study ID between fitting and validation.
Interim analysis is deliberately unavailable in v1: `allowed` is false and
`maximum_looks` is one. A future evidence-bearing schema is required before
scheduled looks or alpha spending can be represented.

Promotion requires equal complete-block weighting, exactly 20,000 deterministic
BCa resamples, at least 12 independent registered blocks, 36 unique
`(block_id, arm_id)` mapped arms, three registered study batches, and a
maximum equal-weight block contribution of 20%. Evaluation independently
re-proves that every mapped creative ID and hash is in the sealed eligible
roster, each ordering contains exactly that roster once, and the comparisons
contain the complete unordered creative-pair set exactly once with directions
consistent with the two orderings. Comparison documents are the closed
projection, so their observation hashes cannot be replayed at this layer.

Every preregistered segment records must-cover membership, effective panel
weight, planned blocks, and approval/evidence hashes. A segment is material
when must-cover or weight is at least 10%. Every material segment is evaluated
separately; sparse evidence limits the result, a weak eligible segment fails
the broad claim, and a clear reversal is a hard negative. Tau and agreement
reversal are evaluated independently and ORed; reversal remains negative when
the segment is also sparse. Adding, removing, or reassigning an observed
segment after outcome access violates the exact block-and-arm plan.

Binary eligible-exposure count and continuous sample count equal eligible
exposures minus missing outcomes. Event-rate exposure time is a separate
positive continuous person-time denominator and need not be integral or equal
the observation count. Effective sample cannot exceed analyzable
observations.

The evaluation v1 contract closes `decision` to `{status}`. Accordingly,
gate reason codes are emitted as stable `reason-code:<code>` entries in the
existing `limitations` list, rather than adding an incompatible
`decision.reason_codes` field. This is the closest faithful representation of
the gate diagnostics under the approved contract.

Evaluation intervals carry explicit availability. Unavailable intervals have
null numeric fields; available intervals retain legitimate all-zero values,
so an available agreement point and upper bound of zero remains a clear
reversal.

The evaluation CLI computes and validates all outputs before it publishes the
evaluation. It rejects any existing or input-aliasing output path and uses
exclusive create with canonical bytes. Evaluation is intentionally published
before an active claim so a collision or filesystem error at claim publication
can leave a valid evaluation without a claim; this safety-favoring partial
publication is recoverable by selecting a new claim path and never overwrites
an existing record.

Evaluation-grade evidence requires `source.permission_confirmed: true`; it
does not admit an unpermissioned descriptive alternative. Count statistics
are non-negative integers, and binary successes cannot exceed eligible
exposures. Tier 4 promotion also requires closed diagnostic statuses that
agree with `all_required_gates_passed`; a leaked, incomplete, insufficient,
dependent, excessive-look, failed, or unstable diagnostic cannot be paired
with a supported promotion.
Any failed or limited material-segment diagnostic also prevents a supported
Tier 4 decision.

`panel-outcome-feedback-v1` remains read-only descriptive feedback. Its
`holdout` Boolean cannot establish Tier 4. An adapter may produce an
evaluation-grade observation only after it supplies and verifies every C1
registration, chronology, source, scope, aggregate, and projection binding.

## Weight boundary

The three weight layers are not interchangeable:

1. A population-frame cell carries a structural weight.
2. A supported overlay carries a conditional allocation within one structural
   group.
3. A reusable profile carries the effective allocation:

   `structural weight × conditional overlay allocation`

Structural weights reconcile to `1.0 ± 1e-9` inside each compatible
partition, relationship, and dimension collection. They never reconcile
across incompatible denominators or unrelated margins and joints. Conditional
overlay allocations reconcile to the same tolerance inside each structural
group. Effective profile allocations reconcile globally. Each dimensional
modeled share is the sum of the available cells with `status: modeled` in its
compatible collection, regardless of whether the source published or a
predeclared rule created the modeled value. The frame summary is the maximum
dimensional modeled share, not a cell count.

Every weight has exactly one semantic label:

- `population_weight`
- `authorized_cohort_weight`
- `planning_allocation`
- `experimental_modeled_weight`

Modeled frame cells use `experimental_modeled_weight`. Conditional overlays
use `planning_allocation`. Effective allocations preserve their structural
group's semantic label.

## `audience-frame-request-v1`

Required top-level fields:

| Field | Shape |
|---|---|
| `schema_version` | `audience-frame-request-v1` |
| `request_id` | canonical identifier |
| `target_audience` | non-empty string |
| `decision` | non-empty string |
| `desired_claim` | non-empty string |
| `geography` | unique non-empty strings |
| `time_basis` | `{as_of, lookback_days}` |
| `target_unit` | canonical identifier |
| `proxy_universes` | array of `{universe_id, description, unit, denominator, exact}` |
| `required_dimensions` | unique canonical identifiers |
| `required_joints` | arrays of at least two declared dimensions |
| `modeled_cell_rules` | closed deterministic modeled-cell declarations |
| `calibration_rules` | closed predeclared calibration declarations |
| `exclusions` | unique strings |
| `authorized_evidence_bases` | unique values from the evidence-basis enum |
| `available_capabilities` | unique canonical identifiers |
| `downgrade_policy` | `{allow_tier_1, allow_experimental, reason}` |

`as_of` is an ISO 8601 calendar date. `lookback_days` is a nonnegative integer.
A required joint cannot introduce a dimension absent from
`required_dimensions`. A modeled-cell rule contains `rule_id`, `unit`,
`denominator`, a nonempty subset of declared `dimension_values`, method
`declared_weight`, `structural_weight`, `{lower, upper}` uncertainty, and a
rationale. A calibration rule contains `rule_id`, `unit`, `denominator`, a
nonempty dimension subset, `calibration_factor`, and rationale. Calibration
factors may be exactly `3.0`; a value above `3.0` fails at the request
boundary.

## `audience-frame-observation-batch-v1`

Required top-level fields:

```text
schema_version, batch_id, frame_request_id, adapter_id, source_family,
source, raw_snapshot_sha256, normalized_batch_sha256, access, geography,
unit, denominator, dimensions, cells, selection_notes, coverage_notes,
citations
```

`source` is:

```text
publisher, program, edition, vintage, retrieved_at
```

`access` is:

```text
access_type, permission_confirmed, permitted_uses
```

`access_type` is `public`, `licensed`, or `authorized`.

Each cell is:

```text
cell_id, dimension_values, estimate, uncertainty, suppressed, status,
relationship, source_location
```

`dimension_values` must contain exactly the batch's declared dimensions.
`uncertainty` is `{lower, upper, method}`. Available unsuppressed `observed`,
`derived`, and `modeled` cells require a finite numeric estimate and finite
numeric bounds with `lower <= upper`.

A `missing` cell, or any cell with `suppressed: true`, requires `estimate:
null`. Its uncertainty bounds may both be `null` when the source does not make
bounds available. In that state, `method` must be a nonempty explanation of
why the bounds are unavailable. The nulls are canonical and must never be
converted to `0` or to a `0/0` interval. Null bounds remain forbidden for
non-missing, unsuppressed cells.

`relationship` is `marginal` or `joint`. Cell status is `observed`, `derived`,
`modeled`, or `missing`.

Observation batches contain structural observations only. They do not contain
profiles, mindsets, creative context, or study quotas.

## `audience-population-frame-v1`

Required top-level fields:

```text
schema_version, frame_id, frame_version, built_at, frame_request_id,
frame_request_sha256, target_universe, proxy_universes, claim_boundary, units,
structural_dimensions, cells, margins, joints, source_bindings,
coverage_assessment, modeled_weight_by_dimension, modeled_weight_share,
eligibility, downgrade_reason
```

`built_at` is a timezone-aware timestamp. `frame_request_sha256` binds the
canonical request. Each unit is an explicit denominator partition:
`{partition_id, unit, denominator, exact}`. Each cell is:

```text
cell_id, partition_id, dimension_values, relationship, origin,
modeled_rule_id, status, structural_weight, weight_semantic, uncertainty,
suppressed, source_observations, calibration_factor
```

`dimension_values` is a nonempty subset of the declared structural dimensions.
Margins carry one dimension and joints carry at least two. `origin` is
`source_observation`, `modeled_rule`, or `explicit_missing`. A modeled-rule
cell binds its `modeled_rule_id`; a source-observation cell binds exact
`{batch_id, cell_id}` identities. Source-published modeled cells remain
`origin: source_observation` with `status: modeled`.

`uncertainty` is `{lower, upper}`. Missing or suppressed cells preserve null
structural weight, semantic, bounds, and calibration; available cells require
finite values. Margin and joint records contain `partition_id`, `dimensions`,
`cell_ids`, and `missing_reason`, so absent collections are explicit. Cells
are bound exactly once to a compatible collection. An empty collection or a
nonempty collection whose referenced cells are all missing or suppressed
requires a nonempty `missing_reason`.

Source bindings preserve the normalized and raw hashes, partition,
publisher/program/edition/vintage/retrieval metadata, geography,
access/permission, selection notes, and coverage notes. Every source
observation resolves to one binding in the same partition as its frame cell.

`coverage_assessment` is:

```text
selection_statement, coverage_statement, known_gaps
```

Eligibility is one of:

- `eligible_tier_2`
- `eligible_tier_3`
- `experimental`
- `no_defensible_frame`

Experimental and no-frame results require a downgrade reason.
`modeled_weight_by_dimension` has one
`{partition_id, dimension, share, status}` row for every weighted
partition/dimension. A share at exactly `0.30` is supported; any value above
`0.30` is experimental and forces an experimental frame. The global
`modeled_weight_share` equals the largest dimensional share. Status and
eligibility derive from the independently calculated share, so a rounded
declaration cannot turn a calculated value above `0.30` into supported.

A canonical `no_defensible_frame` result keeps structural dimensions and
nonempty coverage gaps but has empty units, cells, margins, joints, source
bindings, and dimensional modeled-share rows. Its global modeled share is
zero, and it carries a nonempty downgrade reason. Those empty collections are
valid only in this eligibility state.

## `panel-composition-plan-v1`

Required top-level fields:

```text
schema_version, composition_id, plan_version, built_at, evidence_basis,
requested_tier, achieved_tier, tier_reason_codes, lost_claims, frame_binding,
structural_groups, overlay_hypotheses, profiles, unsupported_combinations,
allocation_constraints, run_allocation_rules, required_diagnostics,
modeled_cell_share
```

`frame_binding` is:

```text
frame_result_sha256, frame_sha256, frame_id, selection
```

The result digest always binds the canonical frame result. For an eligible
frame with an evidence basis other than `none`, the usable-frame digest and
frame ID are nonnull and `selection`
contains exactly `{partition_id, relationship, dimensions}`. That selection
must resolve to exactly one margin or joint collection. Only available,
weighted cells in that selected collection participate in composition;
missing or suppressed cells and cells from every other partition or
collection are excluded. An eligible frame keeps those bindings even when a
used experimental overlay downgrades achieved composition to `tier_1`. For an
experimental or no-frame result, the usable-frame digest, frame ID, and
selection are null and the achieved tier is `tier_1`.

The pure composition builder receives caller-declared
`structural_findings`, `overlay_findings`, and `supported_profile_specs`; it
does not infer additional profile combinations. For an evidence basis other
than `none`, every materializable profile spec has `status: supported` and
nonempty exact support bindings. For `evidence_basis: none`, the same explicit
shape instead has `status: provisional`, empty support bindings, and
reconciling planning allocations. The structural inputs likewise have empty
finding/evidence arrays, and every overlay input is `experimental` with empty
finding/evidence/topic arrays. Tagged `status: unsupported` combinations keep
their reason code and explanation under either route and are never
materialized. A supported/provisional mixture fails closed.

A structural group is:

```text
structural_group_id, origin, cell_ids, structural_finding_ids, evidence_ids,
structural_weight, weight_semantic, must_cover
```

With an eligible frame, groups have `origin: frame_cells`, partition every
available selected cell exactly once, sum only their selected cells, and
preserve one frame semantic. `evidence_basis: none` rejects an eligible frame
entirely rather than translating its cells into provisional support. With an
experimental or no-frame result and an
evidence basis other than `none`, groups have `origin: tier_1_evidence`,
empty `cell_ids`, nonempty finding/evidence support, and
`planning_allocation`; modeled share is exactly zero. When
`evidence_basis: none`, Tier 1 groups instead use
`origin: tier_1_provisional` and have empty structural-finding and evidence
bindings.

An overlay hypothesis is:

```text
overlay_id, description, allocation_basis, finding_ids, evidence_ids,
topic_bindings
```

`allocation_basis` is `observed`, `estimated`, or `experimental`. Every topic
binding is `{topic_id, evidence_ids}` and its evidence is a nonempty subset of
the overlay evidence. With `evidence_basis: none`, overlays are explicitly
experimental and their finding, evidence, and topic bindings are all empty.

An explicit materialized profile is:

```text
profile_id, structural_group_id, overlay_ids, support_status,
support_finding_ids, support_evidence_ids,
conditional_overlay_allocation, overlay_weight_semantic,
effective_profile_allocation, effective_weight_semantic, source_cell_ids
```

For any evidence basis other than `none`, `support_status` is `supported` and
both support lists exactly equal the union of the selected structural group's
support and every referenced overlay's support. Arbitrary, unresolved,
additional, or omitted support IDs fail. For `evidence_basis: none`,
`support_status` is `provisional` and both support lists are empty. A plan
cannot mix provisional and supported shapes.

`source_cell_ids` exactly match the chosen structural group's cells. One
declared group and sorted overlay-set signature may produce at most one
profile. Multi-overlay profiles are valid. Conditional allocations reconcile
within each group, and effective allocation equals structural weight times
conditional allocation. A signature listed in `unsupported_combinations`
cannot appear in `profiles`; each unsupported record contains
`{structural_group_id, overlay_ids, reason_code, reason}`. The validator never
constructs an implicit cross-product.

`requested_tier` accepts tiers 1 through 4; `achieved_tier` accepts tiers 1
through 3. Eligible public evidence caps composition at Tier 2. Eligible
Tier-3 frames with first-party aggregate or hybrid evidence may achieve Tier
3. Experimental/no-frame inputs and any used experimental overlay force Tier
1. A downgrade requires nonempty reason codes and lost claims; an
undowngraded plan requires both lists to be empty. `modeled_cell_share` is
calculated from the selected collection only.

The requested tier is validated independently and may be higher than the
saved panel tier after a downgrade. The saved brief, panel, and final validity
profile bind the achieved tier. Tier 4 remains the held-out-validation layer
over achieved Tier 3 composition.

`run_allocation_rules` is:

```text
reserve_strategy, min_one_for_must_cover
```

The reserve strategy is `largest-remainder` or
`minimum-coverage-first`. A composition plan contains no study-specific quota
count.

## `audience-research-brief-v3`

V3 preserves every field and behavioral rule from
`audience-research-brief-v2` and adds:

```text
panel_tier, evidence_basis, workflow_state_binding,
population_frame_result_sha256, population_frame_sha256,
authorized_audience_import,
structural_findings, overlay_findings, claim_boundary,
dimensional_validity, scoped_approvals
```

An authorized import is null or:

```text
handoff_schema_version, handoff_sha256, status, cohort_id,
exact_cohort_denominator, selection_statement, coverage_statement,
max_calibration_factor
```

The handoff schema is `authorized-audience-handoff-v1`; status is `complete`
or `complete_with_loss`. Tier 3 requires this record. Other tiers may bind one
without changing their tier or evidence basis.

Each dimensional-validity row is
`{dimension, status, limitations}`. Each scoped approval is
`{scope, status, target_sha256}`, where status is `approved` or `rejected`.
When `evidence_basis` is `none`, `structural_findings` and
`overlay_findings` are empty, `panel_tier` is `tier_1`, and
`population_frame_sha256` is null. Otherwise both finding lists are nonempty.

`validate_research_brief_v3` is the public standalone brief boundary. It
validates the exact v3 allowlist, projects the canonical document to its
unchanged v2 shape, and invokes the authoritative v2 brief validator. All
brief-local v2 checks remain active. The validator returns a canonical deep
copy and never mutates the caller's input.

## `saved-audience-panel-v3`

V3 preserves every field and behavioral rule from `saved-audience-panel-v2`
and adds:

```text
panel_tier, evidence_basis, brief_id, population_frame_sha256,
population_frame_result_sha256, composition_plan_sha256, validity_profile_sha256,
authorized_handoff_sha256, audit_binding, claim_boundary, package_status
```

`package_status` is `unpackaged`, `proposed`, or `approved`.

`audit_binding` is a strict tagged union. The Release B1 variant is:

```text
applicability, auditor_run_id, audit_sha256, report_inputs_sha256,
evidence_ledger_sha256, finding_support_sha256,
synthesis_matrix_sha256, report_manifest_sha256
```

`applicability` is exactly `release_b1`. Every digest in this object is a bare
lowercase SHA-256 digest. The audit digest is computed from the independently
validated canonical audit. The four legacy evidence/report digests supply the
expected legacy inputs; they are not copied from the audit under review.

The migration-only variant is exactly:

```json
{
  "applicability": "legacy_v2_migration",
  "status": "not_available",
  "source_package_sha256": "sha256:<64 lowercase hex>",
  "reason": "<nonempty truthful limitation>"
}
```

It is valid only for a Tier 1, unpackaged panel whose usable population-frame
and authorized-handoff bindings are null. This record describes audit
unavailability. It is not research evidence and does not claim that the
migrated panel passed a Release B1 audit. Fields from the two variants cannot
be mixed.

Regardless of audit-binding variant, a saved panel with
`evidence_basis: none` must be Tier 1 and must have a null
`population_frame_sha256`. The standalone panel validator rejects a
none-plus-eligible-frame state before it can be presented to full
cross-document validation.

`validate_saved_panel_v3` is the public standalone saved-panel boundary. It
first validates the exact V3 extension allowlist, projects that canonical
panel to its unchanged V2 shape, and runs the authoritative V2
`validate_saved_panel` contract. It preserves every panel-local check,
including panel name/segments, scope fingerprint, privacy confirmation,
provenance, weights, replicates, governance, timestamps, and unknown fields.
Because no brief is supplied, it defers only `unresolved_finding` and
`finding_evidence_mismatch`. Full cross-document validation still resolves
and enforces both checks. The standalone validator returns a canonical deep
copy and does not mutate the input.

## Honest v2-to-v3 migration

`migrate_v2_to_v3` accepts one existing `audience-panel-package-v2` archive,
reads it through the public `read_validated_package_archive` boundary, and
therefore validates and safely reads one exact immutable byte snapshot. The
requested panel version must be a strictly newer canonical
`MAJOR.MINOR.PATCH` version. Numeric components have no leading zero unless
the component is exactly zero.

Migration emits documents only:

```text
audience-research-brief-v3.json
saved-audience-panel-v3.json
panel-composition-plan.json
panel-validity-profile.json
migration-provenance.json
```

It does not emit a population-frame file, observation batch, source record,
outcome record, calibration result, package manifest, or v3 archive. Package
generator `2.0.0` remains a Release B2 responsibility. When those migrated
documents are packaged, the package must embed canonical
`migration-provenance.json` and the original validated v2 archive so the
validator can rerun the deterministic migration and compare every target
document exactly.

Every migrated result is Tier 1 with `population_frame_sha256: null` and
`package_status: unpackaged`. A canonical `no_defensible_frame` result is
stored inside migration provenance so the composition and validity documents
can bind and pass the v3 validators without claiming that a usable population
frame exists. That result has no units, cells, margins, joints, source
bindings, or modeled weight. A no-research source remains honestly
provisional: it uses `evidence_basis: none` and the empty-support composition
shape documented above. Migration metadata is not promoted into research
evidence.

Evidence-backed v2 modes preserve their copied evidence and map explicitly:

```text
public_research         -> public
crm_first_party         -> first_party_aggregate
hybrid_research         -> hybrid
provisional_no_research -> none
```

The `use_existing_saved_panel` and `user_provided_research` modes do not
identify a unique v3 evidence route. Migration rejects them before
publication and requires an explicit rebuild instead of guessing.

For an evidence-backed route, structural groups, overlays, and profiles bind
only real v2 finding and evidence IDs; profile support is `supported`. For the
no-research route, brief finding lists and every group, overlay, topic, and
profile support list are empty; groups use the Tier 1 provisional origin and
profiles use provisional support. The migrator never substitutes metadata,
fallback IDs, or generated identifiers for missing research evidence.

The inherited v2 segment and context weights remain planning inputs only.
Segment weights become Tier 1 structural-group `planning_allocation` values.
Context-stratum weights become conditional planning allocations. When one v2
stratum has multiple explicit grounded profiles, its conditional planning
allocation is split equally because v2 has no within-stratum profile-prevalence
field. The migrator creates exactly one v3 composition profile for each
existing `grounded_profile_id`; it creates no implicit combinations.

`audience-panel-v2-to-v3-migration-v1` provenance records:

- the original archive byte count and exact SHA-256;
- the original manifest, brief, and panel SHA-256 bindings;
- the old and new panel versions and all emitted document hashes;
- the canonical no-defensible-frame result;
- segment, context, and grounded-profile planning-allocation mappings;
- explicit limitations and the fact that no v3 archive was created; and
- the explicit fact that a Release B1 audit was not available for the legacy
  package.

The migrated panel uses the `legacy_v2_migration` audit-binding variant. It
records the exact source-package digest and a truthful limitation instead of
inventing an auditor identity or hashes for audit/report/evidence documents
that do not exist.

The migration command never rewrites the original archive or copies it into
its five-document output directory. A later authenticated v3 package does
embed those original bytes as source authority. Migration publication is
no-clobber. The migrator serializes all five documents into a private staging
directory and claims the destination with an exclusive directory creation.
Every destination component is opened without following symlinks. Only
verified Darwin root aliases `/var -> /private/var` and `/tmp -> /private/tmp`
are canonicalized; environment-derived temporary paths never create a symlink
exception.

The parent, staging directory, and claimed output remain pinned by directory
descriptors. Staged files are created and transferred relative to those
descriptors, so a pathname replacement cannot redirect document writes. The
migrator records the claimed device and inode and verifies both the parent
path and output path against their pinned descriptors immediately before
success. A concurrent directory creator wins without replacement. On failure,
known files are removed through the pinned descriptors, and a public directory
name is removed only when its no-follow device and inode still match the
migration's claim. An independently installed replacement is never deleted.

## `panel-construction-audit-v2`

Release B1 adds the top-level field `applicability: release_b1` and adds
`population_frame_result_sha256` to the closed input bindings. Exact
frame-result, composition-plan, and final-validity digests are required. The
usable-frame digest is nullable only for Tier 1. Authorized handoff is
required only when the selected route binds one.

Population-frame traceability and weight-semantics checks are active in every
Release B1 audit. Authorized-handoff traceability is `not_applicable` exactly
when its binding is null and active otherwise. Evidence paths remain
creative-blind and may reference only the canonical brief, panel, ledger,
finding support, synthesis, report manifest, population frame, composition
plan, validity profile, or authorized handoff surfaces. Creative, evaluation,
performance, campaign-outcome, and calibration-result paths are forbidden.

The existing `panel-construction-audit-v1` contract and Release A fixture
remain unchanged.

## `panel-validity-profile-v1`

Required top-level fields:

```text
schema_version, validity_id, panel_id, panel_tier, evidence_basis, axes,
binding_state, predeclared_validation_design, held_out_outcome_evidence,
source_bindings
```

`axes` contains exactly:

- `structural_frame`
- `overlay_evidence`
- `allocation_fidelity`
- `outcome_calibration`
- `external_validation`

Each axis is `{status, coverage, limitations}`. Coverage is null or a finite
ratio from 0 through 1. Axis status uses the closed runtime enum for
unavailable evidence, insufficient evidence, directional support, supported
evidence, or held-out validation.

The validity document rejects these field names recursively:

```text
confidence, confidence_score, overall_score, composite, percentage
```

No axis is averaged, summed, or converted into one score.

`predeclared_validation_design` is null or:

```text
design_id, registered_at, holdout_definition, metrics
```

`binding_state` is `frame_provisional` or `panel_final`. A provisional record
has null panel identity, tier, evidence basis, brief, panel, and composition
bindings. It requires `frame_result_sha256`; `frame_sha256` is nonnull only
when the result is a usable eligible or experimental frame, and then the two
digests match.

A final record requires panel identity, tier, evidence basis, and exact brief,
panel, frame-result, and composition digests. Tier 1 permits
`frame_sha256` to be null or to equal `frame_result_sha256`; this allows the
standalone shape to represent either a no-frame route or an eligible-frame
overlay downgrade. Every higher tier requires the two frame digests to match.
Cross-document validation derives the one exact allowed value from the
canonical frame result and composition selection. The brief and panel digests
bind their unchanged v2 projections so the new binding does not alter v2
bytes or create circular hashes.

## `panel-outcome-feedback-v1`

Required top-level fields:

```text
schema_version, feedback_id, panel_id, study_id, variant_id, cohort_id,
metric, metric_direction, units, windows, aggregate, design, source,
holdout, missingness, limitations, source_sha256
```

Nested shapes:

```text
metric:    name, definition
units:     exposure, outcome
windows:   measurement, attribution
aggregate: numerator, denominator, value
source:    source_id, permission_confirmed
```

Metric direction is `higher_is_better`, `lower_is_better`, or `descriptive`.
Design is `experimental`, `observational`, or `modeled`. Aggregate numeric
fields are nullable, but at least one value must be supplied and a supplied
denominator must be positive.

The outcome-feedback binding layer applies the stricter aggregate completeness
rule needed for calibration review: if `value` is null, both `numerator` and
`denominator` are required. Either count may be absent only when `value` is
present. `missingness` and the nonempty `limitations` list remain explicit in
both cases.

Validation returns only:

```json
{
  "canonical_copy": {"schema_version": "panel-outcome-feedback-v1"},
  "source_digest": "sha256:..."
}
```

The abbreviated object above illustrates the envelope; `canonical_copy`
contains the complete validated input. Outcome validation rejects `score`,
`rank`, `profile_weight`, `frame_weight`, and `panel_version` recursively.
It cannot modify scores, rankings, frame weights, profile weights, panel
versions, population frames, composition plans, or saved panels.

## `panel-outcome-feedback-binding-v1`

`bind_outcome_feedback` first validates the exact standalone
`saved-audience-panel-v3` surface and every supplied feedback document through
the authoritative `validate_outcome_feedback` validator. Every feedback
`panel_id` must equal the saved panel ID, every permission confirmation must be
true, and one binding contains exactly one `study_id`.

Variants, metrics, and cohort IDs may differ inside that one study. One
`cohort_id` has one exact exposure unit and measurement window inside the
binding; reusing the label with either value changed is an incompatible cohort
identity and fails. One `source_id` likewise has exactly one `source_sha256`;
conflicting source bytes fail. This study-scoped rule permits one authorized
study to carry explicit subgroup results without allowing a subgroup label to
change universes silently.

Required top-level fields:

```text
schema_version, binding_id, bound_at, panel_binding, study_id, variant_ids,
cohort_identities, metric_identities, source_identities, feedback_records,
limitations, binding_sha256
```

`panel_binding` records the exact panel ID, semantic version, canonical panel
hash, tier, evidence basis, frame-result and usable-frame hashes, composition
and validity hashes, optional authorized-handoff hash, claim boundary, and
package status. Each feedback record preserves the complete canonical feedback
document plus its own canonical hash, exact study/variant/cohort identity,
metric definition and direction, exposure and outcome units, measurement and
attribution windows, aggregate values, design label, source identity and hash,
holdout flag, missingness, and limitations. `evaluation_set` is `held_out`
when `holdout` is true and `in_sample` otherwise.

Records sort by `feedback_id`; identity summaries and limitations use stable
canonical ordering. `binding_sha256` is calculated over the complete binding
with `binding_sha256` set to null. The binding contains no saved panel and
performs no score, rank, frame, composition, or weight update.

## `panel-calibration-refresh-proposal-v1`

`propose_calibration_refresh` revalidates the exact saved panel, recomputes the
complete feedback binding from its canonical documents, and verifies the
binding and panel hashes before emitting a proposal. Its status is exactly
`requires_calibration_approval` and `executable` is false.

The proposal contains only panel and feedback bindings, the evaluation scope,
review items, limitations, and this empty non-executable diff:

```json
{
  "base_panel_sha256": "sha256:...",
  "proposed_panel_sha256": null,
  "operations": []
}
```

`proposal_sha256` uses the same null-self-hash procedure as the binding. The
proposal cannot contain changed scores, ranks, profile weights, or frame
weights. Any actual calibration or panel change requires a new versioned
refresh after separate calibration approval.

## Cross-document validation

`validate_audience_research_v3`:

1. validates the v3 extensions;
2. projects the brief and panel to their byte-compatible v2 shapes and invokes
   the unchanged v2 pair validator;
3. validates the required population-frame result, composition, and validity
   profile;
4. verifies every digest and identity binding;
5. enforces the tier-specific frame and authorization rules;
6. dispatches on the exact `audit_binding` variant.

For `release_b1`, the validator requires the existing workflow state to be
approved and requires a real Release B1 `panel-construction-audit-v2` to pass.
For `legacy_v2_migration`, it requires both `workflow_state` and
`construction_audit` to be null. Migration still validates the brief, panel,
frame result, composition, validity profile, and every binding among those
five documents. Supplying workflow/audit documents beside a migration
binding, or omitting either document beside a Release B1 binding, fails.
Neither a passing audit nor mutually consistent hashes can authorize an
`evidence_basis: none` panel to retain an eligible usable frame.

The validator independently derives bare v2 brief and panel digests. Workflow
brief/panel bindings and the `evidence_synthesis`/`panel_construction` approval
targets must equal those digests. The workflow report-input and audit bindings
must equal the panel's closed `audit_binding`; its package binding is null
because this validation stage performs no packaging.

The brief and panel always bind `population_frame_result_sha256`. For an
eligible result, the usable `population_frame_sha256` is the same digest,
including an overlay-driven Tier 1 downgrade. For an experimental or no-frame
Tier 1 result, the result digest remains nonnull while the usable-frame digest
is null. Standalone Tier 1 brief, panel, and final-validity shapes permit
either nullable form; cross-document validation derives the exact expected
value from the canonical frame result and composition selection and rejects
null/non-null swaps. Cross-document validation accepts only a `panel_final`
validity profile.

Release B1 audit expectations are independently derived: v2 brief/panel
digests, the four legacy evidence/report digests from `audit_binding`, the
canonical frame-result digest, nullable usable-frame digest, canonical
composition digest, canonical final-validity digest, and route-appropriate
authorized-handoff digest. Candidate audit bindings are never used as their
own expectations.

For Tier 3, `exact_cohort_denominator` must equal an exact denominator in the
frame. Every frame cell's calibration factor must be no greater than both 3.0
and the authorized handoff's declared `max_calibration_factor`.

The return tuple always has seven positions. It contains canonical brief and
panel copies, the canonical frame result (including a no-frame result),
composition, validity, workflow state, and construction audit. The final two
positions are `None, None` for an honest legacy migration. No adapter
acquisition, frame-building algorithm, package resolution, quota allocation,
calibration update, or ad-testing behavior occurs in these validators.

## Release B2 dashboard allocation binding

A v3 study dashboard accepts allocation evidence only through
`study-manifest.json.outputs.audience_allocation_jobs`. The index has exactly
these top-level fields:

```json
{
  "schema_version": "audience-allocation-jobs-index-v1",
  "screening": {},
  "boundary": {},
  "finalist": {}
}
```

A dispatched screening or finalist record has exactly `status`, `path`,
`content_hash`, and `record_count`. The path is respectively
`screening-jobs.json` or `finalist-jobs.json` at the run root. The content hash
is the prefixed SHA-256 of the raw file bytes, and the count equals the exact
number of `synthetic_replicate_jobs`. `{"status":"not_dispatched"}` is the
only non-dispatched shape and is invalid when accepted responses or results
exist for that stage. An allocation decision document that exits before
worker-ready jobs is never a dispatched envelope.

Partial-exposure boundary dispatch has exactly `status: dispatched` and
`waves`. Wave records are ordered and contiguous from 1 and contain exactly
`wave`, `path`, `content_hash`, and `record_count`. Canonical filenames are
`boundary-wave-0001-jobs.json`, `boundary-wave-0002-jobs.json`, and so on.
Validate every successful envelope against the frozen boundary reserve and
canonical audience resolution, then require its selected slot IDs to extend
the prior successful envelope by exactly its newly authorized slots. The
latest cumulative subset is the boundary run-claim authority.

Accepted response evidence is a separate exact binding boundary. Screening and
finalist responses bind to their validated stage envelopes. Boundary responses
bind to the ordered union of newly authorized jobs from every validated wave;
the latest cumulative subset remains the allocation-claim authority, not the
response lookup set. Use the shared response/job binding validator with
`require_exact_set=False`: incomplete accepted subsets are allowed, but every
accepted response must match the exact job's replicate, response, dispatch,
record type, assignment, order, and canonical response contract. Additionally,
`audience_slot_id`, `grounded_profile_id`, and `profile_snapshot_sha256` must
exactly match that job. When `dispatch-audit.jsonl` is present, use the shared
dispatch-audit binding validator to reconcile every bound job as accepted or
exhausted under the manifest retry policy.

Complete exposure instead requires exactly:

```json
{
  "status": "not_applicable",
  "reason": "method_complete_exposure"
}
```

It forbids boundary results, accepted boundary responses, and a boundary job
binding or file. The dashboard displays exactly `Not applicable — complete
exposure has no boundary stage` and shows no boundary percentages,
must-cover status, or fidelity claim.

Full-roster diagnostics come only from the validated frozen Task 4 roster.
Selected-for-dispatch diagnostics come only from the validated subset in a
manifest-bound, successful Task 5 job envelope. For a partly dispatched
boundary or finalist reserve, show both scopes but treat only the selected
scope as realized run-claim authority. Do not recompute weights in the
dashboard template.

A rendered dashboard with `audience.run_allocation` has exactly one canonical
Run allocation section, one allocation body, and one canonical text-only
renderer. A dashboard without that payload has none of those v3-only surfaces.
The standalone validator enforces both directions so removing, substituting,
or duplicating the visible allocation report fails closed.

Approved Tier 1 panels remain reusable. Their structural and overlay values
retain directional planning semantics and do not use the
five-percentage-point frame gate. Only provisional panels remain run-local.
When a structural-frame allocation is distorted, the user must choose whether
to increase capacity, approve a scope merge or exclusion and rebuild, or
continue directionally. The system never chooses on the user's behalf.

The exact claim language is:

- Directional profile allocation: `This reusable Tier 1 panel allocates
  synthetic panelists across approved profiles using directional planning
  allocations. It does not claim population composition.`
- Frame aligned: `The synthetic roster is aligned to the approved structural
  frame within the product allocation threshold.`
- Allocation distorted: `The requested synthetic capacity cannot preserve the
  approved structural composition within the product threshold.`
- Directional continuation: `This run remains a Tier 1 directional creative
  hypothesis stress test even though the saved panel retains its approved
  reusable tier.`
- Always: `This is not a human sample or a customer survey.`

The five-percentage-point value is a product allocation threshold, never a
margin of error. Target weights, raw slot shares, and analysis-effective
shares are allocation diagnostics, not survey results, population prevalence
beyond the approved frame, measured performance, or confidence.

## Release C1 held-out ordering validation boundary

Release C1 consumes, but never mutates, an authenticated v2 or v3 audience
panel package. Its validation package is a separate deterministic archive that
binds the exact base-panel package, frozen synthetic ordering, preregistration,
aggregate observations, comparisons, claim family, held-out evaluation,
plain-language report, source inventory, and either an active narrow claim or
an honest negative result.

Evaluation and claim documents are replayed from the authenticated producer
evidence before packaging or report rendering. The packaged report must be
the exact static canonical projection of that replayed result; a safe,
self-hashed alternate HTML file is not accepted.

The independent unit is the preregistered validation block. Exact metric, unit,
window, cohort, placement, creative, panel, package, run, result, and scope
bindings must match. Sparse proportion, continuous, and event-rate boundaries
retain uncertainty and cannot promote a claim. Coverage, missingness, sample
sufficiency, power, leakage, multiplicity, repeated looks, and
material-segment sparsity or reversal fail closed.

Influence is descriptive, not a promotion or veto gate. The evaluation still
records every leave-one-block and leave-one-batch tau, agreement, one-sided
raw p-value, and whether the registered point/raw-p thresholds remain met.
These descriptive rows do not claim to recompute BCa or claim-family Holm
gates. They make a concentrated result visible in reports and dashboards.

Evaluation applies the stricter of the built-in and preregistered minimum-block
and minimum-coverage thresholds. A validation archive contains exactly the
observation set embedded in its packaged comparison collection, and its
evaluation embeds that exact comparison collection. Its shared outcome file is
the canonical projection of the first evaluated observation, so audit files
cannot be swapped for unrelated self-hashed evidence.

An active Tier 4 claim means only that held-out aggregate evidence supports
using the frozen panel ordering to prioritize creatives within the registered
scope. It is not an absolute score, threshold, success probability, CTR,
conversion, revenue, or lift prediction. Finalist shares remain descriptive.
The optional Ad Testing Lab display reads the authenticated package and the
authoritative claim-library lifecycle at render time. It may show an active
claim, expiry, disclaimer, and refresh triggers, or an honest negative,
limited, invalid, expired, superseded, withdrawn, invalidated, or
not-yet-active, or registered-but-not-current state; it does not change any
testing calculation or output.

Claim packages are immutable. Expiry, supersession, withdrawal, and
invalidation are append-only registry events bound to the original claim.
Registration validates one private snapshot and publishes those exact source
bytes with atomic create-if-absent semantics. Lifecycle recovery commits old
and new event-log byte hashes and can complete only the one authorized partial
append.
Package publication validates all members in a private sibling staging
directory before publishing the complete directory. An interrupted package
build leaves no final output. If registration publishes an exact immutable
package before an interrupted index write, an exact retry recognizes and
indexes that orphan; different bytes still fail closed. Operating-system file
locks coordinate writers without stale-lock unlinking.

Lifecycle events append only in strictly increasing effective-time order.
Registering another claim for the same scope does not change `current`; the
earliest active registration remains current until an authenticated
supersession event names an exact registered replacement. Reports and
dashboards compare both package ZIP and manifest hashes to the authoritative
library entry before displaying an active claim.
Release C1 performs no panel adjustment or calibration and does not begin
Release C2.
