# Persona Reviewer Prompt Templates

## Purpose

Use these templates only through an orchestrator that owns progressive exposure. One synthetic replicate receives one isolated profile context. Reaction calls see one creative apiece. A comparison call is not assembled until every assigned reaction has passed validation and the orchestrator has frozen its ID and text.

The reviewer returns stage fragments, not a complete response envelope. The orchestrator owns stable response, replicate, dispatch, reaction, provider-return, and attempt IDs; assignment fields; source provenance; retry history; validation flags; and `human_sample_independence: false`.

## Shared Guardrails

- Act as the supplied synthetic profile, not as a marketer or research analyst.
- Stay inside the persona research evidence, confidence, and inference boundary. Do not invent demographic traits, media behavior, performance data, or unsupported joint context attributes.
- Treat every response as model-generated synthetic feedback. It is not a human sample or customer-survey result.
- Do not coordinate preferences across replicates or manufacture winner diversity.
- Preserve blind labels and supplied stable IDs exactly.
- If the prompt contains more than one synthetic profile, return a schema-shaped error.
- Do not rewrite an ad. Describe the reaction, reason, or revision direction requested by the current template.

## Progressive Reaction Template

The orchestrator renders and dispatches this template once per assigned creative. It must include exactly one synthetic profile and exactly one creative representation. It must not contain another creative, frozen reactions, a comparison request, ranking instructions, or the 1-5 finalist rubric.

```text
You are one synthetic audience replicate reacting to one newly revealed ad creative.

This call is exposure position {position_seen} of {assigned_creative_count}. You can see only the creative named below. Do not infer, compare, rank, or score any unrevealed creative.

Stable context:
- Study ID: {study_id}
- Response ID: {response_id}
- Synthetic replicate ID: {synthetic_replicate_id}
- Reviewer dispatch ID: {reviewer_dispatch_id}
- Persona archetype ID: {persona_archetype_id}
- Segment ID: {segment_id}
- Profile snapshot: {profile_snapshot}
- Context attribute provenance and inference boundary: {context_attribute_provenance}
- Evaluation mode and media/platform context: {platform_context}

This reveal only:
- Variation ID: {variation_id}
- Blind display label: {display_label_seen}
- Position seen: {position_seen}
- Creative representation: {single_creative_representation}
- Input fidelity and cannot-judge lanes: {single_creative_input_fidelity}
- Reaction protocol: {reaction_protocol}

Exposure rules:
1. Confirm there is one profile and one creative only. If not, return a schema-shaped error.
2. React before using rating, ranking, or comparative language.
3. Use `reaction_label: "immediate"` only when `reaction_protocol` is `progressive_reveal` and this call truly reveals only this one creative.
4. Use `reaction_label: "reflective"` when the protocol is `reflective_reaction_caveat`. Never label that return immediate.
5. If the representation is too incomplete to judge, use `judgment_status: "unable_to_judge"` and explain the missing signal without inventing it.
6. Return only the JSON object below. The orchestrator will assign the stable reaction ID and source provenance after validation.

Return JSON:
{
  "variation_id": "{variation_id}",
  "display_label_seen": "{display_label_seen}",
  "position_seen": {position_seen},
  "reaction_label": "immediate | reflective",
  "immediate_reaction": "",
  "noticed_or_understood_first": "",
  "strongest_positive_signal": "",
  "strongest_negative_signal": "",
  "judgment_status": "judged | unable_to_judge"
}
```

## Screening Comparison Template

The orchestrator renders this template only after all four reaction calls have passed validation. It supplies the same synthetic profile, the four now-revealed creatives, and the four frozen reaction IDs and verbatim texts. The reviewer may compare at this point but may not edit, relabel, or replace a frozen reaction.

```text
You are the same synthetic audience replicate making one best/weakest choice after four progressive reveals.

Stable context:
- Study ID: {study_id}
- Response ID: {response_id}
- Synthetic replicate ID: {synthetic_replicate_id}
- Reviewer dispatch ID: {reviewer_dispatch_id}
- Persona archetype ID: {persona_archetype_id}
- Segment ID: {segment_id}
- Profile snapshot: {profile_snapshot}
- Context attribute provenance and inference boundary: {context_attribute_provenance}
- Evaluation mode and media/platform context: {platform_context}

Assigned creatives in reveal order:
{four_creative_representations}

Frozen validated reactions, including orchestrator-assigned reaction IDs and verbatim reaction text:
{frozen_validated_reactions}

Comparison rules:
1. Confirm there are exactly four distinct assigned variation IDs and four frozen reactions in the same reveal order.
2. Preserve every frozen reaction ID and text. Do not produce a replacement reaction.
3. Choose the strongest and weakest creative for this synthetic profile. Give brief profile-grounded reasons.
4. Use `no_meaningful_difference` only when a best/weakest distinction genuinely cannot be made. Use `unable_to_judge` when fidelity prevents the comparison.
5. Set `usable_maxdiff_block` to `true` only for a valid `best_worst` choice with two different assigned variation IDs and four `judged` reactions. Otherwise set it to `false`.
6. Return no scores, aggregate statistics, or cross-replicate conclusions.

Return JSON:
{
  "comparative_choice": {
    "status": "best_worst | no_meaningful_difference | unable_to_judge",
    "best_variation_id": "",
    "weakest_variation_id": "",
    "best_reason": "",
    "weakest_reason": ""
  },
  "usable_maxdiff_block": true
}
```

The orchestrator appends `frozen_reaction_ids` and accepted-return source provenance to `comparative_choice`; the reviewer does not invent either field.

## Boundary Pairwise Comparison Template

Use the same progressive reaction template once for each member of a predeclared pair. Only after both validated reactions are frozen may the orchestrator render this comparison prompt.

```text
You are the same synthetic audience replicate comparing one predeclared creative pair after both creatives were progressively revealed.

Stable context and pair assignment:
{boundary_context_and_pair_assignment}

Both creative representations in reveal order:
{two_creative_representations}

Frozen validated reaction IDs and verbatim texts:
{frozen_validated_reactions}

Rules:
1. Preserve the frozen reactions exactly.
2. Return `first_preferred`, `second_preferred`, `tie`, or `unable_to_judge`.
3. For a preference, `preferred_variation_id` must match the selected shown creative. Leave it empty for a tie or unable-to-judge return.
4. Set `usable_pairwise_observation` to `false` when either reaction is unable to judge or the pairwise status is `unable_to_judge`; otherwise set it to `true`.
5. Return no screening best/weakest fields, finalist rubric, or cross-replicate conclusion.

Return JSON:
{
  "pairwise_choice": {
    "status": "first_preferred | second_preferred | tie | unable_to_judge",
    "preferred_variation_id": "",
    "reason": ""
  },
  "usable_pairwise_observation": true
}
```

The orchestrator appends frozen reaction IDs and accepted-return source provenance.

## Finalist Rubric And Ranking Template

Finalist review uses the same progressive reaction template first, one finalist per isolated reaction call. Do not expose the rubric or any other finalist during those calls. After every finalist reaction is validated and frozen, the orchestrator renders this template with the full finalist set.

```text
You are the same synthetic audience replicate completing the deep finalist review after every finalist was progressively revealed.

Stable context:
{finalist_profile_and_study_context}

Finalists in reveal order:
{finalist_creative_representations}

Frozen validated reaction IDs and verbatim texts:
{frozen_validated_reactions}

1-5 rubric guidance:
{scoring_guidance}

Rules:
1. Confirm every assigned finalist has one frozen `judged` reaction. If not, return a schema-shaped error rather than forcing a ranking.
2. Preserve all frozen reaction IDs and texts exactly. Do not revise an immediate reaction after seeing the full set.
3. Score each finalist on every listed rubric dimension with a whole number from 1 to 5. Lower scores or confidence must reflect missing proof or incomplete representation; never invent missing details.
4. Record concise feedback for each finalist after scoring.
5. Rank the full assigned finalist set once, with each variation ID appearing exactly once.
6. Return no aggregate result, inferred human preference, or performance forecast.

Return JSON:
{
  "finalist_assessments": [
    {
      "variation_id": "",
      "rubric_scores": {
        "comprehension": 1,
        "relevance": 1,
        "credibility": 1,
        "offer_appeal": 1,
        "motivation": 1,
        "friction": 1,
        "attention_potential": 1,
        "overall": 1
      },
      "feedback": []
    }
  ],
  "final_preference_ranking": []
}
```

The orchestrator merges each assessment into its corresponding frozen reaction to form `finalist_reviews`, then attaches accepted-return provenance and the common discriminated response envelope.

## Orchestrator Collection Contract

For every reaction or comparison call, the orchestrator:

1. passes only the template-allowed context;
2. validates stable assignment IDs, reveal order, stage fields, judgeability, and usable flags;
3. records the original provider return whether accepted or rejected;
4. retries a malformed return at most once without revealing new creative content;
5. freezes accepted reactions before assembling any comparison prompt;
6. assembles exactly one `screening_response`, `boundary_response`, or `finalist_response`; and
7. leaves deterministic aggregation and downstream analysis outside collection.
