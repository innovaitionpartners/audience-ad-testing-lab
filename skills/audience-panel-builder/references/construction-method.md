# Panel Construction Method

## Contents

1. Compilation layers
2. Segments and mindsets
3. Context strata and explicit profiles
4. Population-frame boundaries
5. Explicit reusable composition
6. Weighting
7. Creative-naive construction
8. Blind construction audit
9. Approval and refresh

## Compilation Layers

Compile each profile from separate evidence layers:

1. **Structural:** role family, seniority, industry, company size, geography, and buying role.
2. **Operating:** responsibilities, workflows, KPIs, constraints, and current pressures.
3. **Decision:** trigger, stage, criteria, perceived risk, objections, proof needs, and stakeholders.
4. **Media:** discovery behavior, channel use, content preferences, and exposure context.
5. **Social/community:** current language, emerging themes, questions, objections, proof demands, peer signals, and platform norms.
6. **First-party:** approved aggregate customer, CRM, interview, sales, support, community, and owned-social findings.
7. **Performance:** relevant historical creative and outcome patterns, never current-candidate outcome leakage.

For every material field retain evidence IDs, field date, audience and market applicability, confidence, inference boundary, and one status:

```text
observed, estimated, experimental
```

## Segments And Mindsets

A segment is a researched cluster supported by an approved segment hypothesis. A buyer mindset is an evidence-backed decision posture within a segment.

Do not:

- derive a segment from one viral post;
- turn a topic cluster into an audience-size estimate;
- name a mindset after an alignment judgment;
- encode the reaction the panelist is expected to give;
- use invented psychographic color.

Use role and industry as visible structural context, not buried flavor text. Use mindset for decision posture, not job title.

Shared category findings may establish background conditions, but they do not
by themselves establish multiple distinct mindsets. For every materially
distinct archetype, retain at least one finding specific to that archetype or
use a narrower cohort finding in a finding set that actually distinguishes it
from peer archetypes. If the available evidence is genuinely omnibus and the
distinction is still useful as an estimated planning hypothesis, declare each
finding's scope in its existing inference boundary as `Evidence scope:
cross-audience.`, `Evidence scope: cohort:<segment-id>.`, or `Evidence scope:
profile:<archetype-id>.` Do not infer scope from citation placement. Put the
exact structured exception `Evidence-specificity exception
[unsupported_distinction=<what is not supported>; missing_research=<what
evidence is absent>; bounded_use=<what limited use remains>]: <justification>`
in the affected archetype's existing inference boundary. Never copy one
complete source/finding set across profiles and present repetition as
profile-level corroboration.

## Context Strata And Explicit Profiles

A context stratum is a planned allocation within one segment. A grounded profile is one explicit archetype-and-stratum combination.

Do not form the Cartesian product of:

- title;
- industry;
- company size;
- geography;
- buying stage;
- mindset;
- urgency;
- proof preference.

Separately supported traits do not prove a joint person. Create only combinations that have joint support or label the combined dimension `experimental`.

Every profile must answer, in plain language:

- What role and industry context does this profile represent?
- What is happening in the buying situation?
- What does this profile need, fear, question, and require as proof?
- Which source IDs support each material attribute?
- Which parts are observed, estimated, or experimental?

## Population-Frame Boundaries

Build structural population frames only from validated canonical observation
batches. Keep every unit and denominator in its own explicit partition.
Persons, firms, establishments, households, accounts, campaigns, and exposures
are different universes; their counts and weights never share a denominator.

Bind each retained source's raw and normalized hashes, publisher, program,
edition, vintage, retrieval timestamp, geography, access, permission,
selection statement, and coverage statement. Every geography carried by a
source batch must be inside the approved request geography; partial
intersection does not authorize a mixed in-scope/out-of-scope batch. A source
outside the approved geography or vintage window, a mismatched request, or
unconfirmed permission cannot silently contribute to a frame.

Materialize every observed, source-published modeled, suppressed, and missing
cell with its exact origin. A missing critical joint stays visible as a
partitioned empty joint with a reason. A required joint whose cells are wholly
missing or suppressed is equally missing-critical and forces the exact
downgrade reason. Never manufacture category values to make a missing joint
appear observed.

An available collection whose source estimates total zero cannot establish
relative structural weights. Record a `nonpositive-collection-total` gap and
omit that unusable collection from the frame; do not divide by zero, assign
invented weights, or relabel an observed zero as missing. Other defensible
collections may still support a frame.

Only a modeled-cell rule declared in the approved frame request may create a
new modeled cell. Its declared weight reserves that share inside one compatible
partition and dimension collection; source estimates normalize over the
remainder. Calculate modeled effective weight per partition and dimension,
using weight rather than cell count. Exactly 30% remains supported. More than
30% is experimental and blocks a Tier 2 or Tier 3 composition claim.

Authorized-cohort calibration rules are also predeclared. A factor of exactly
`3.0` may pass; any factor above `3.0` is rejected before construction.

State claim boundaries literally:

- A public frame represents only the named public proxy universes, not the full
  commercial target audience.
- An authorized frame represents only the exact permissioned cohort, not
  people outside it.
- Social/community and other overlay evidence informs hypotheses and language,
  never structural prevalence.

When no defensible frame exists, emit the canonical no-frame result with empty
units, cells, margins, joints, and source bindings plus exact downgrade
reasons. Preserve Tier 1 by binding the no-frame result hash while keeping the
usable population-frame reference null. Do not convert absence, suppression,
or unavailable uncertainty to zero.

Tier 3 requires usable source-observed structural support in an exact
authorized partition whose unit is the request's target unit. An unrelated,
wholly unavailable, or zero-total authorized partition cannot elevate a usable
public proxy to Tier 3.

The report renderer dispatches matching canonical v2 and v3 brief/panel
schemas. For a Tier 1 v3 package built from an experimental or no-defensible
frame result, validate the composition against that exact canonical result,
require the composition and validity profile's usable `frame_sha256` to remain
null, and bind the result through `frame_result_sha256`. If an eligible frame
is downgraded to Tier 1 only because a used overlay is experimental, retain
the exact usable-frame digest and selected collection. Release A v2 rendering
remains unchanged.

## Explicit Reusable Composition

Select exactly one eligible margin or joint collection in one unit and
denominator partition. Structural groups must exactly partition the available,
weighted cells in that collection. Missing or suppressed cells and cells from
other partitions do not become structural groups. Every structural group
retains its finding IDs, evidence IDs, structural weight, weight semantic, and
must-cover flag.

When the canonical frame result is experimental or no defensible frame exists,
use explicit Tier 1 evidence groups. These groups have no source cell IDs, use
only evidence-backed planning allocations, and reconcile to one. Do not create
a synthetic population-frame result or infer group prevalence from the number
of findings, sources, profiles, or model calls.

Keep overlay hypotheses separate from structural prevalence. Every overlay
must bind decision-relevant finding and evidence IDs, at least one topic
binding, and one allocation basis:

```text
observed, estimated, experimental
```

Reject unrelated affinity evidence. A topic binding may reference only
evidence already bound to that overlay.

Construct only the profile signatures the caller explicitly supplied. Each
signature names one structural group and one nonempty, explicitly supported
set of overlay IDs. A multi-overlay profile requires its own joint support;
separate support for each overlay is not enough. Reject a complete or implicit
structural-group-by-overlay Cartesian product. Record caller-declared
unsupported signatures with exact reason codes rather than creating them.

Conditional overlay allocations reconcile inside each structural group.
Calculate, never accept, effective profile allocation as:

```text
structural group weight × conditional overlay allocation
```

The conditional overlay semantic is always `planning_allocation`. The
effective semantic preserves the structural group's `population_weight`,
`authorized_cohort_weight`, `experimental_modeled_weight`, or
`planning_allocation`. This decomposition permits a frame-grounded structural
claim while keeping a directional overlay allocation honest.

The reusable composition may carry must-cover constraints, a stable
largest-remainder strategy, and diagnostics requirements. It never carries a
study quota, slot count, panelist count, requested capacity, or capacity
ceiling. Release B2 owns run-specific allocation.

Composition can achieve at most Tier 2 from an eligible public proxy and at
most Tier 3 from an eligible authorized frame with compatible first-party or
hybrid evidence. A no-frame or experimental frame, or any used experimental
overlay, forces Tier 1. The function never constructs Tier 4. Every downgrade
records requested tier, achieved tier, reason codes, and the exact claims that
were lost.

Finalize the dimensional validity profile only after real panel identity,
panel tier, evidence basis, exact brief and panel-projection digests, and the
canonical population-frame result and composition plan exist. Finalization
verifies the provisional frame-result binding, derives the usable-frame and
composition digests from those canonical documents, copies the five validity
axes without mutation, and returns a newly validated `panel_final` document.

## Weighting

Weights require a defensible denominator from an appropriate survey, structural source, or permissioned first-party distribution.

Without one, use:

```text
weighting_rule: planning_allocation
```

Planning allocation exists to cover useful test contexts. It is not audience share.

Never weight from:

- post, comment, review, or follower counts;
- likes, views, shares, or engagement;
- number of sources mentioning a theme;
- number of synthetic profiles or model calls.

## Creative-Naive Construction

Reusable panels must be built before and independently of the creative roster. Reject any profile field that contains:

- a preferred ad or expected winner;
- expected scores or rank;
- desired verbatim wording;
- creative-specific claims or knowledge;
- positive or negative reaction priors;
- questionnaire overrides.

The profile prompt may contain the approved compiled context. The Ad Testing Lab response prompt remains unchanged.

## Blind Construction Audit

After panel construction and before canonical packaging or registration, run a
blind construction audit against the approved brief, saved panel, evidence
ledger, finding support, synthesis matrix, and research-report manifest. The
audit records traceability and construction validity only. It does not receive
creative, evaluation output, performance output, campaign outcomes, winner
labels, or private model reasoning.

Release A binds the six available research documents and records population
frame, composition plan, validity profile, and authorized handoff bindings as
`null`. Those unavailable seams are `not_applicable`; they do not license a
population or calibrated-performance claim. Population composition is not
available in Release A. An approved v2 package remains a Tier 1
evidence-grounded panel; it supports a Directional creative hypothesis stress
test. Synthetic panel output is not a customer survey or a human sample. A
failed audit blocks canonical packaging and registration.

The deterministic document-aware gate validates the exact brief, panel,
ledger, finding support, synthesis matrix, and report manifest; recomputes all
six available digests and four required `null` bindings; resolves every audit
path, finding, and profile reference; and then requires a passing result. It
never accepts the audit's own binding claims as proof of the documents being
audited.

Release B1 uses `panel-construction-audit-v2` with
`applicability: release_b1`. The document-aware gate independently validates
and hashes the canonical population-frame result, usable frame when present,
composition plan, and `panel_final` validity profile. It verifies the final
validity profile's exact frame-result, usable-frame, and composition bindings.
When an authorized handoff is present, the gate binds its exact canonical hash
and verifies that canonical output names retain their declared semantic route.
The authorized-handoff check is `not_applicable` only when no handoff is
bound.

The Release B1 audit must cover:

- every selected frame cell and structural group under population-frame
  traceability;
- every explicit reusable profile under profile traceability;
- every overlay hypothesis under inference boundaries;
- every structural group and reusable profile under weight semantics; and
- every non-outcome auditable canonical output under authorized-handoff
  traceability when a handoff is bound.

Outcome feedback remains in its separate feedback lane. Its output is covered
only by the exact authorized-handoff manifest hash, never cited as construction
evidence.

Audit paths may reference only approved research documents, the canonical
population frame, composition plan, final validity profile, and optional
authorized handoff. Creative, evaluation, performance, score, rank, winner,
conversion, revenue, and calibration outputs remain outside the blind audit.

## Approval And Refresh

At approval, show:

- plain-language research summary and proof points;
- source coverage and gaps;
- proposed segments and mindsets;
- role, industry, company-size, and buying-situation coverage;
- every evidence-backed weight versus planning allocation;
- social-data contribution and limitations;
- unsupported and experimental combinations;
- allowed and prohibited uses.

Approval binds the exact brief and panel version. A material change creates a new version. Never overwrite an approved package.

Refresh when:

- the audience, market, category, or buying context changes;
- scheduled age or evidence-age limits pass;
- a material source is retracted or its permitted use changes;
- first-party evidence materially contradicts construction;
- field calibration shows persistent segment or context error.
