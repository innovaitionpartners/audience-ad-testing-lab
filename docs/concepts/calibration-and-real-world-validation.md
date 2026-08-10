# Calibration and real-world validation

Synthetic testing, real-world outcome validation, experimental real-world panel calibration, and the fictional calibration sandbox are separate evidence lanes.

## Synthetic testing

Ad Testing Lab freezes an audience, creative set, protocol, and method, then produces a model-conditional ordering and qualitative feedback. This is the prediction or hypothesis to preserve before outcomes exist.

## Real-World Outcome Data Prep

Data Prep preserves the study before launch and imports aggregate platform results afterward. It protects chronology, provenance, source integrity, and readiness. It stops before evaluation and does not decide whether the panel was right.

Read [Prepare and validate with real results](../guides/validate-with-real-results.md).

## Real-World Outcome Validation

Validation compares a preregistered synthetic ordering with eligible, permissioned, aggregate held-out outcomes. It can evaluate ordering and pair direction under the frozen claim family while checking:

- chronology and preregistration;
- creative and audience bindings;
- independent validation blocks;
- eligibility and missingness;
- uncertainty and coverage;
- power and multiplicity;
- repeated looks and leakage;
- material-segment requirements.

Only an authenticated passing evaluation can issue a narrow, expiring claim. Negative, limited, invalid, and descriptive-only evaluations remain visible and distinct.

Validation does not create a master score, predict absolute performance, or rewrite the original panel, prompts, synthetic responses, scoring, or aggregation.

## Experimental Real-World Panel Calibration

From the user's perspective, this is a guided two-phase improvement workflow. The user selects the saved panel and provides or identifies aggregate results from eligible completed studies. Audience Panel Builder determines whether those results show a repeated panel-behavior miss, creates at most one bounded candidate, and shows the exact change. The user later provides aggregate results from a fresh held-out campaign and approves or rejects the exact new version. The user does not assemble the internal evidence graph or modify panel files.

Repeated authenticated validation misses can begin a separate Audience Panel Builder route. It requires at least two disjoint negative validation packages that are bound to the same immutable base package and pass the non-result evidence gates. A creative-attribute registry frozen before outcomes must support exactly one existing-persona, one-field hypothesis, and the review must explicitly clear attribution, delivery, landing-page, offer, targeting, timing, and tracking explanations.

The route diagnoses the repeated miss, proposes one bounded behavior update, and materializes a complete newer panel candidate with an exact diff and provenance. The original panel remains byte-for-byte unchanged. The candidate then needs a new, nonoverlapping held-out Real-World Outcome Validation evaluation. It can be registered as a new version only when the fresh evaluation supports the exact candidate package, every evidence gate passes, the claim remains active, and a human explicitly approves both the sealed calibration proposal and the exact package registration.

This route remains experimental. A passing candidate evaluation supports only the registered ordering and scope. It does not prove that the persona edit caused the result, validate absolute performance, or authorize silent activation.

The older `panel-calibration-refresh-proposal-v1` is a non-executable descriptive seam. It is not calibration evidence and cannot create a candidate. The authoritative route consumes authenticated Real-World Outcome Validation packages instead.

## Experimental Persona Behavior Calibration Sandbox

The sandbox is an internal known-answer engineering harness. It uses fictional synthetic fixtures and a separately held answer key to test protected engineering mechanics. It can show whether the pipeline detects a known synthetic condition without leaking the answer. It does not invoke or authorize the real-evidence route.

It cannot be registered or activated as an audience panel. It also cannot establish:

- real-world operability;
- panel validity;
- market behavior;
- production calibration;
- authority to register, activate, or modify a panel.

In other words, it cannot establish real-world operability, cannot establish panel validity, cannot establish market behavior, and cannot establish production calibration.

Calling the sandbox “calibration” does not make it real-world evidence.

## Evidence ladder

```text
Synthetic creative stress test
        ↓ preserves a preregistered hypothesis
Aggregate real campaign outcomes
        ↓ eligible held-out validation
Narrow expiring validation claim, if supported
        ↓ repeated disjoint misses + bounded diagnosis
Complete versioned panel candidate with exact diff
        ↓ fresh nonoverlapping held-out validation
Explicit human approval + gated new-version registration
```

Each arrow is a governed handoff. No stage silently upgrades the evidence produced by the earlier one.

## Related documentation

- [Real-World Outcome Data Prep](../../skills/real-world-outcome-data-prep/README.md)
- [Audience Panel Builder](../../skills/audience-panel-builder/README.md)
- [Synthetic evidence and validity](synthetic-evidence-and-validity.md)
- [Privacy and data boundaries](../reference/privacy-and-data-boundaries.md)
