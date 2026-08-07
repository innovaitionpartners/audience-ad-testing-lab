# Audience Research Synthesis Method

## Contents

1. Method basis
2. Required synthesis matrix
3. Question-by-question procedure
4. Confidence assessment
5. Contradictions and negative cases
6. Proof-point selection
7. Segment sufficiency
8. Marketer translation
9. Worked example: conflicting evidence
10. Worked example: unsupported segment
11. Quality checklist

## Method Basis

Synthesize around the research questions, not around source types. A list of
survey findings followed by a list of social themes is not integration.

This method adapts:

- the UK Magenta Book requirement to integrate findings around each question,
  examine conflicts, consider explanations, and seek more evidence when useful:
  https://www.gov.uk/government/publications/the-magenta-book/magenta-book-central-government-guidance-on-evaluation-html
- GRADE-CERQual's four confidence components: methodological limitations,
  relevance, coherence, and adequacy:
  https://www.cerqual.org/overall-confidence-in-the-evidence/
- Cochrane's guidance on thematic or framework synthesis and transparent
  confidence assessment:
  https://www.cochrane.org/authors/handbooks-and-manuals/handbook/current/chapter-21
- CDC qualitative-analysis guidance on familiarization, coding, theme
  refinement, within/between-case analysis, and supported conclusions:
  https://www.cdc.gov/evaluation/php/evaluation-framework-action-guide/step-5-generate-and-support-conclusions.html
- mixed-methods joint displays that put different evidence strands in one
  analytic structure rather than merely reporting them beside one another:
  https://pmc.ncbi.nlm.nih.gov/articles/PMC4097839/
- market-research standards requiring transparent AI use, data origins,
  limitations, human judgment, and enough technical detail for independent
  assessment:
  https://www.insightsassociation.org/About-Us/Code-of-Standards/Resources/Code-of-Standards

Use the method as an auditable decision framework. Do not mechanically average
qualitative confidence components or turn source counts into statistical
certainty.

## Required Synthesis Matrix

Create `audience-synthesis-matrix-v1` before drafting the research brief.
Validate its exact item linkage with:

```bash
python3 scripts/validate-synthesis-matrix.py \
  <ledger.json> <finding-support.json> <synthesis-matrix.json>
```

For each research question, the matrix records one or more candidate findings:

```text
finding_id, statement, category, evidence_item_ids,
supporting_item_ids, qualifying_item_ids, contradicting_item_ids,
integration_state, methodological_limitations, relevance, coherence,
adequacy, confidence, confidence_reason, inference_boundary,
marketer_implication, creative_implications, segment_decision
```

Allowed integration states:

```text
convergent, complementary, mixed, discordant, single_source
```

Allowed confidence-component values:

```text
no_serious_concerns, minor_concerns, major_concerns, unknown
```

Allowed segment decisions:

```text
candidate, emerging_hypothesis, gap_only, not_segment_relevant
```

## Question-By-Question Procedure

For each planned research question:

1. Read every accepted evidence item assigned to the question.
2. Extract the narrowest claim each item can support. Preserve population,
   geography, field dates, method, and decision context.
3. Group dependent items by upstream source. A press article and vendor post
   summarizing one survey remain one evidence family.
4. Place supporting, qualifying, and contradicting items in the same joint
   display.
5. Compare what differs: population, role, market, buying stage, question
   wording, field period, channel, or evidence method.
6. Write a candidate finding no broader than the matched evidence.
7. Assess confidence components and state the reason.
8. Decide whether the finding supports a segment, an emerging hypothesis, a
   documented gap, or no segmentation decision.
9. Translate the accepted finding into one marketer implication and one or more
   creative implications without adding a claim.

If no evidence can support a useful finding, record a gap. Plausibility is not
evidence.

## Confidence Assessment

Assess each finding separately.

### Methodological limitations

Consider recruitment or coverage, question wording, missing methods, unclear
denominators, sponsor influence, social-search boundaries, bot/spam controls,
and whether the source is primary or derivative.

### Relevance

Consider match to the named audience, geography, market, buying situation,
decision, and time period. Adjacent evidence can qualify a finding; it cannot
silently become exact evidence.

### Coherence

Ask whether the finding explains the contributing items without ignoring
important variation or negative cases. A coherent finding may be conditional,
such as “implementation risk becomes more salient during vendor evaluation.”

### Adequacy

Consider both richness and quantity. One directly matched, transparent study
may be more useful than many thin mentions, but sparse or shallow evidence
limits confidence.

### Overall confidence

- **High:** no serious concerns across all four components and the finding is
  supported by adequate independent evidence.
- **Medium:** the finding is likely useful for the named decision, but at least
  one component has meaningful limitations.
- **Low:** support is sparse, indirect, methodologically weak, materially
  discordant, or suitable only as an emerging hypothesis.

Social-only support cannot receive high confidence. Confidence describes how
much weight to give the finding in panel construction, not a probability that
the statement is true.

## Contradictions And Negative Cases

Never delete a contradiction to make a cleaner narrative.

Classify the relationship:

- **Convergent:** independent evidence supports materially the same bounded
  finding.
- **Complementary:** evidence addresses different parts of the same decision
  and expands the finding without conflict.
- **Mixed:** the main pattern holds, but material qualifying or contradicting
  cases remain.
- **Discordant:** sources materially conflict and the difference is not yet
  explained.
- **Single source:** only one independent evidence family supports the finding.

For mixed or discordant evidence:

1. inspect audience, stage, geography, timing, and method differences;
2. narrow or split the finding only when the moderator has evidence;
3. lower confidence when the conflict remains;
4. seek targeted evidence when the decision warrants it;
5. preserve the unresolved conflict as a gap if it cannot be adjudicated.

Do not average two incompatible findings into a vague midpoint.

## Proof-Point Selection

A proof point is evidence a marketer or technical reviewer can inspect.
Select it only when it:

- directly supports or materially qualifies an accepted finding;
- retains the primary source URL and methodology link when available;
- states the matched population and time frame;
- preserves the original denominator for any number;
- separates verbatim text from an analyst summary;
- adds decision value beyond repeating another upstream source.

For every major finding, show:

```text
What the evidence says
Why it matters for this buying decision
Best proof point and source
Important qualification or contradiction
Confidence and inference boundary
Panel-construction consequence
Creative implication
```

## Segment Sufficiency

A segment candidate must pass every gate:

1. **Differentiation:** it represents a material role, context, need, risk, or
   decision posture that differs from another candidate.
2. **Decision relevance:** the difference could change what the profile notices,
   questions, needs as proof, or considers risky.
3. **Evidence lineage:** approved findings and exact evidence items support the
   distinction.
4. **Combination support:** the proposed role/context/mindset combination is
   observed together, defensibly estimated, or explicitly experimental.
5. **Descriptive sufficiency:** evidence supports needs, objections, triggers,
   and proof requirements without invented psychographics.
6. **Allocation honesty:** a defensible denominator supports the weight, or the
   weight is labeled planning allocation.

If a candidate fails, merge it, keep it as an emerging hypothesis, label the
combination experimental, or exclude it. Do not create a segment merely to
increase panel variety.

## Marketer Translation

Translate each accepted finding with this compact structure:

```markdown
### [Plain-language finding]

**What we found:** [bounded finding]
**Why it matters:** [effect on the named buying decision]
**Proof point:** [source-backed evidence]
**What complicates it:** [qualification or contradiction]
**Panel implication:** [segment, mindset, situation, or no construction change]
**Creative implication:** [what an ad should prove, clarify, or avoid]
**Confidence:** [high/medium/low] — [reason and boundary]
```

Keep one study-level directional-scope note. Do not repeat the same synthetic or
directional disclaimer beneath every finding.

## Worked Example: Conflicting Evidence

**Evidence**

- A directly matched weighted survey reports acquisition cost as the most
  frequently selected vendor criterion.
- Aggregate win/loss interviews say late-stage evaluators most often stalled
  over migration risk.
- Current professional-community posts ask for implementation timelines and
  customer proof, but the corpus is not representative.

**Bad synthesis**

> Buyers care about cost and implementation.

This erases stage, source strength, and the apparent conflict.

**Defensible synthesis**

```text
Finding: Cost shapes initial vendor screening, while implementation risk becomes
more salient during active evaluation.
Integration: complementary
Confidence: medium
Boundary: directly supported for the surveyed and interviewed populations; the
social evidence supplies current language, not prevalence.
Panel consequence: create a buying-situation distinction only if stage is
supported in the approved target frame; do not create a new demographic segment.
Creative implication: make migration proof visible for evaluation-stage profiles
without assuming price is irrelevant.
```

The stage explanation must be supported. If the interview records do not record
stage, classify the evidence as discordant and preserve the uncertainty.

## Worked Example: Unsupported Segment

**Evidence**

- Three high-engagement posts use skeptical language about AI automation.
- Author roles and company context are unknown.
- No survey, structural source, or approved first-party evidence shows a stable
  subgroup with a distinct buying process.

**Wrong output**

> Segment: AI Skeptics — 25% of the panel.

**Correct output**

```text
Finding: Skepticism about AI automation appears in current discussion.
Confidence: low
Integration: single_source
Segment decision: emerging_hypothesis
Inference boundary: language signal only; no role, incidence, or stable subgroup
is established.
Panel consequence: no reusable segment and no weight.
Creative implication: test whether concrete control and implementation proof
answers the objection across already supported profiles.
```

## Quality Checklist

- Every finding maps to exact support, qualification, and contradiction items.
- Dependent sources count as one evidence family.
- Confidence is assessed per finding, not per report.
- Negative cases remain visible.
- Numbers preserve population, denominator, field period, and source.
- Segment candidates pass every sufficiency gate.
- Marketer implications do not add claims.
- Social engagement has zero prevalence weight.
- Unsupported claims become hypotheses or gaps.
- The synthesis matrix validates before brief approval.
