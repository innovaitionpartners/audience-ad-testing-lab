# Worked Route Examples

All names, sources, counts, findings, and identifiers below are fictional.

## Contents

1. Research plan
2. Source decisions
3. Research brief
4. Panel construction
5. Refresh diff
6. Audit-only result

## Research Plan

**Request:** Build a U.S. panel of mid-market B2B operations leaders who are
evaluating workflow software. The user supplies aggregate win/loss themes and a
permissioned Sprinklr export.

**Response 1 excerpt:**

```markdown
## Audience research plan ready for approval

- Route: `create_research_backed_panel`
- Audience and buying decision: U.S. mid-market operations leaders evaluating
  workflow software
- Research tier: `standard`
- Evidence basis: `hybrid`
- Verified read capabilities: saved listening topics, date filters, pagination

| Research question | Evidence lane | Planned sources | Known gap |
|---|---|---|---|
| Which roles and operating contexts define the audience? | structural | O*NET, Census business data | None |
| What triggers evaluation and what proof reduces risk? | survey + first-party | current business survey families, aggregate win/loss themes | Buying-stage denominator is not supplied |
| What language and objections appear now? | social/community | permissioned Sprinklr export | Corpus coverage is bounded by the saved topic |

Approve this plan before collection or synthesis.
```

The plan contains no audience finding. Selected source-family records are leads
to retrieve and assess.

## Source Decisions

```markdown
| Candidate | Decision | Why | Allowed use |
|---|---|---|---|
| Weighted U.S. operations survey with direct role cut | accept | Exact audience and decision match; transparent method | Bounded survey finding |
| Aggregate win/loss theme package | accept_with_limits | Direct client context; nonprobability case set | Triggers, objections, proof needs |
| Sprinklr saved-topic export | accept_as_qualitative | Current language with documented collection | Language and emerging questions only |
| Trade article repeating the survey | reject | Same upstream study | None; avoids false corroboration |
```

Each accepted row resolves to exact evidence-item IDs. The decisions do not
turn source quality scores into finding confidence.

## Research Brief

```markdown
### Implementation proof matters most during active evaluation

**What we found:** Directly matched survey evidence and aggregate win/loss
themes indicate that implementation risk becomes a material concern during
active vendor evaluation.

**Why it matters:** An evaluation-stage buyer may screen out a credible product
when migration effort and ownership are unclear.

**Proof point:** The survey's named operations-leader cut, with its population,
field dates, denominator, and method link.

**What complicates it:** The win/loss cases are not a representative sample,
and the social corpus supplies current wording rather than incidence.

**Panel implication:** Preserve an evaluation-stage buyer situation. Do not
create a demographic segment unless stage is observed in the target frame.

**Creative implication:** Make migration steps, implementation ownership, and
credible customer proof easy to inspect.

**Confidence:** medium — directly relevant evidence converges on the decision,
but prevalence and stage composition are not established.
```

The marketer brief links the proof point and exposes the qualification. The
technical appendix retains the exact ledger, support roles, and synthesis
matrix.

## Panel Construction

This fictional approved brief supports one segment, one mindset, two buyer
situations, and two explicit grounded profiles. It does not support an implicit
cross-product with every title and industry.

```text
segment:
  operations-evaluation-buyers
  weighting_rule: planning_allocation

mindset:
  proof-seeking-operator
  evidence: finding-implementation-proof

situations:
  early-replacement-scan
  active-vendor-evaluation

explicit profiles:
  proof-seeking-operator + early-replacement-scan
  proof-seeking-operator + active-vendor-evaluation
```

For the active-evaluation profile, `decision_context`, implementation anxiety,
and proof needs resolve to the approved finding. “Healthcare VP Operations” is
not added merely because healthcare and VP titles appear separately in source
data. That joint combination would need resolving evidence or an
`experimental` label.

## Refresh Diff

```markdown
| Field or finding | Existing | Proposed | Exact evidence | Reason | Downstream effect |
|---|---|---|---|---|---|
| implementation-proof confidence | medium | high | new direct survey + repeated approved win/loss study | independent directly matched evidence resolved the earlier adequacy concern | no new segment; stronger proof-needs provenance |
| AI-control objection | not represented | emerging hypothesis | current social items only | language is new but subgroup stability is unknown | no reusable profile or weight |
```

The old package remains immutable. An approved material change produces a new
semantic version.

## Audit-Only Result

```markdown
# Panel Audit: operations-leaders/2.1.0

## Decision
`needs_refresh`

## Evidence and synthesis
- Two core findings resolve to accepted evidence.
- One profile combines industry, title, and stage without joint support.
- The source-family registry is current, but one underlying survey edition has
  been superseded and must be rechecked.

## Construction
- Segments: pass
- Mindsets: pass
- Situations: pass
- Explicit profiles: fail on unsupported combination

## Required action
Use the refresh route. This audit does not edit, package, or register a panel.
```
