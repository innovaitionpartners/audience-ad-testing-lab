# Test a finished creative set

## Use this when

You want structured synthetic feedback on 2–100 existing ad variations and a governed shortlist for a real test. Use [Ad Testing Lab](../../skills/audience-ad-testing-lab/README.md).

This workflow evaluates ads. It does not generate new ads unless you separately request creative development after the evaluation.

## What you need

- Two to 100 exact finished variations in one format: copy, static image, carousel, or represented video.
- A saved audience package or a plain-language audience description for the provisional route.
- Campaign decision, offer, goal, funnel stage, buying context, and success metric.
- Inspectable imagery for every image, carousel card, thumbnail, keyframe, or represented-video frame.

Strategy notes, landing-page fragments, themes, and message territories are not test-ready creatives. The system must show the creative inventory and obtain confirmation rather than assembling its own roster.

## 1. Freeze inputs

The workflow assigns stable IDs, preserves exact copy, and hashes inspectable media. It records whether context and evidence were directly observed, estimated, experimental, summarized, or missing.

## 2. Choose the method

- **Two to six creatives:** complete exposure. Every synthetic execution reviews the complete set.
- **Seven to 100 creatives:** partial exposure after a burden pilot. Each execution reviews a deterministic balanced subset, and only an unclear frozen cutoff may receive a separate boundary wave.

The planner chooses profile-aware capacity from the creative design and grounded profiles. It does not multiply segment count by a universal number or treat profile count as respondent count. Read [Methods and capacity](../reference/methods-and-capacity.md).

## 3. Approve the frozen run plan

Before dispatch, review:

- the exact creative roster and hashes;
- audience version and grounded profiles;
- assignments and exposure order;
- core synthetic executions and balanced reserves;
- minimum usable-feedback floors;
- worker isolation and retry policy;
- cost and latency range;
- intended attention target for imagery.

For a provisional audience, this is the only audience-construction approval before initial collection.

## 4. Collect structured feedback

Each authorized synthetic job receives a frozen audience slot and creative assignment. Fresh contexts reduce cross-response leakage but do not create human-sample independence.

The system preserves:

- accepted feedback records;
- raw provider returns;
- rejected attempts and validation errors;
- dispatch and retry lineage.

## 5. Aggregate deterministically

Prompts do not calculate scores, utilities, stability, or shortlist membership. Deterministic tools apply the frozen method and return explicit validity or refusal states.

When the evidence cannot support a roster, `exploratory`, `invalid`, `incomplete`, or `unresolved` is the correct result.

## 6. Approve finalists

Before approval, finalist metrics remain empty and no ad is described as moving forward. After approval, fresh synthetic jobs review the complete finalist set.

## 7. Generate attention evidence

For imagery, the workflow creates or imports an original/overlay pair for every inspectable representation after finalist approval. Attention evidence diagnoses whether the intended offer, proof, CTA, product, or brand cue receives visual emphasis. It does not change the shortlist.

`copy_only` is the sole normal omission route. Missing or invalid attention evidence for every inspectable media representation outside that route is a hard stop before dashboard rendering.

## 8. Open the dashboard

The dashboard leads with the decision and retains every source export under Downloads. Read [Outputs and files](../reference/outputs-and-files.md).

## Next step

Use the shortlist to design a real campaign. Before outcomes exist, [prepare and freeze the real study](validate-with-real-results.md).
