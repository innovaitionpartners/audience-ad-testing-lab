# Calibration and real-world validation

Synthetic testing, real-world outcome validation, and the experimental calibration sandbox are separate evidence lanes.

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

## Later behavior-calibration proposals

Sufficient fresh real evidence may motivate a bounded proposal about one modeled behavior. That proposal remains separately reviewed and cannot silently modify or activate an audience panel.

## Experimental Persona Behavior Calibration Sandbox

The sandbox is an internal known-answer engineering harness. It uses fictional synthetic fixtures and a separately held answer key to test protected engineering mechanics. It can show whether the pipeline detects a known synthetic condition without leaking the answer.

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
        ↓ separate human review
Possible bounded behavior proposal
```

Each arrow is a governed handoff. No stage silently upgrades the evidence produced by the earlier one.

## Related documentation

- [Real-World Outcome Data Prep](../../skills/real-world-outcome-data-prep/README.md)
- [Synthetic evidence and validity](synthetic-evidence-and-validity.md)
- [Privacy and data boundaries](../reference/privacy-and-data-boundaries.md)
