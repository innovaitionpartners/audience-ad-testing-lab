# Private-Data Modeling Policy

## Contents

1. Modeling order
2. Segment candidates
3. Performance prediction
4. Synthetic tabular data
5. Downstream interpretation

## Modeling Order

Use this order:

1. data quality and coverage;
2. descriptive distributions and cross-tabs;
3. privacy-safe cohort outcomes;
4. exploratory segmentation when a specific decision needs it;
5. predictive modeling when a named outcome and valid holdout exist;
6. prospective validation when the use warrants it.

More complex is not automatically more valid.

## Segment Candidates

Use approved numeric metrics and categorical dimensions after rare-value grouping. Never include:

- entity or direct identifiers;
- quasi-identifiers unless a separate review explicitly approves them;
- sensitive traits;
- outcomes that would define the audience by its future response;
- candidate-ad performance.

Evaluate multiple `k` values and deterministic seeds. Report:

- usable rows and features;
- minimum and maximum cluster sizes;
- separation score;
- silhouette separation of at least `0.10`;
- pairwise co-assignment stability of at least `0.70` across seeds;
- privacy suppression;
- feature summaries that distinguish each candidate;
- sensitivity to feature removal when robust research is requested.

Reject candidates with cells below the privacy minimum or materially unstable assignments. Accepted outputs remain `exploratory_segment_candidate`.

## Performance Prediction

Define:

- outcome;
- entity and observation unit;
- prediction time;
- feature availability at prediction time;
- train period;
- holdout period;
- baseline;
- decision metric.

Use an earlier training partition and later holdout when time is material. Report baseline and model performance on the same untouched holdout. Do not use random cross-validation as the only evidence for a temporal marketing decision.

Detect:

- post-outcome features;
- audience or delivery fields that encode campaign treatment;
- target leakage;
- duplicated entities across partitions;
- unsupported causal interpretation;
- severe class imbalance;
- sparse subgroup results.

The strongest automatic status is `retrospectively_evaluated`. Temporal or prospective validation is approved outside this data-preparation run.

## Synthetic Tabular Data

Use SDV or another synthesizer only when synthetic microdata is actually needed for controlled sharing, software testing, or downstream analysis. Prefer a direct aggregate or differentially private statistic when the questions are known.

Assess separately:

- utility: marginal distributions, pairwise relationships, and task performance;
- privacy: disclosure protection, nearest-record distance, overfitting, and the declared attacker model;
- governance: allowed recipients, retention, and reuse.

Synthetic rows are not consumers, respondents, or synthetic panelists. They are a privacy-preserving representation of tabular patterns.

## Downstream Interpretation

Audience Panel Builder decides whether aggregate patterns support a segment, mindset, situation, or weight. Ad Testing Lab decides whether a frozen panel and protocol align with a named performance outcome. Audience Data Lab does not make either decision.
