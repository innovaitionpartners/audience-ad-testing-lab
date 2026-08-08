# Profiles, synthetic replicates, model calls, and people

Several different counts appear in an Ad Testing Lab run. Keeping them separate is essential.

## Grounded profiles

A grounded profile is an evidence snapshot describing one supported role or context inside an audience segment. It can contain needs, objections, operating conditions, buying context, media habits, decision patterns, evidence links, and explicit unknowns.

A profile is not a literal person. Six profiles do not mean the panel contains six respondents.

## Synthetic executions or replicates

A synthetic execution is one authorized run-specific review job using a frozen profile snapshot and creative assignment. The planner may create multiple balanced executions from the same grounded profile.

This repetition helps assess whether the protocol produces stable model-conditional feedback across isolated contexts. It does not create six, 30, or 36 independent human opinions.

## Accepted feedback records

An accepted feedback record is a synthetic execution whose complete response passed the contract and validation rules. A dispatched execution can fail or exhaust retries without producing an accepted composite response.

## Model calls

One synthetic execution can require multiple model calls because reactions may use progressive reveal, comparisons, or retries. `total_model_calls` is therefore not the number of synthetic executions and not the number of people.

## Archetypes, segments, and context strata

- A **segment** is a user-meaningful audience cohort.
- A **grounded profile** represents one supported role or context within a segment.
- An **archetype** is a modeled behavioral or decision pattern used for sensitivity analysis.
- A **context stratum** preserves a supplied segment/context identity through planning and reporting.

These labels can overlap in meaning, but their identifiers and denominators remain distinct.

## Human respondents

Human respondents are always zero in the synthetic workflow. Fresh worker contexts reduce cross-response leakage; they do not create human-sample independence. This repository-level methodology disclosure does not need to be repeated in routine run plans or results when the output consistently labels the units as synthetic.

Every marketer-facing run plan and result should separate:

- grounded profiles;
- planned isolated synthetic executions;
- minimum usable feedback records;
- accepted feedback records;
- model calls.

## Why the planner can reuse profiles

The profile is the grounded context. The synthetic executions are the run-specific applications of that context to a frozen creative design. Reusing a profile across balanced executions is intentional, but duplicated or near-duplicated responses cannot be counted as extra stability units.

Capacity depends on the creative method, profile weights, usable-feedback floors, reserves, connectivity, and sensitivity requirements—not on a universal “respondents per segment” rule. Read [Methods and capacity](../reference/methods-and-capacity.md).

## What the numbers support

The counts support auditability and conditional run-stability checks. They do not support human-sample statistical significance, market prevalence, or population forecasting.

## Related guides

- [Build an audience panel](../guides/build-an-audience-panel.md)
- [Test ads](../guides/test-ads.md)
- [Synthetic evidence and validity](synthetic-evidence-and-validity.md)
