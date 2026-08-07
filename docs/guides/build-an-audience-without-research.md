# Test ads without audience research

## Use this when

You have exact finished creatives and want an immediate directional stress test, but you do not have a saved audience panel or time to build a reusable research-backed one.

This route belongs to Ad Testing Lab. It does not send you through a separate Audience Panel Builder approval workflow.

## What you need

- The exact creatives to test.
- A plain-language description of the audience.
- Any cohort distinctions that must remain separate.
- Campaign context, offer, goal, funnel stage, and success metric.

If the creatives do not exist yet, the system can retain a draft audience scope, but it will not materialize a provisional panel or invent ads from strategy documents.

## How the provisional audience is built

The system creates the smallest scope supported by what you actually supplied:

- one segment for each cohort you explicitly distinguish;
- one grounded profile for each materially different role or context you explicitly describe inside that cohort;
- exactly one segment and one grounded profile when you provide only one audience phrase.

It does not proliferate unsupported persona detail to make the panel look richer. Unknown fields remain `unknown`.

## Approval and expiry

There is no research-plan approval, research-brief approval, or reusable-package approval. Those would be empty formalities without research.

The frozen run plan is the approval surface. It shows the audience lock, profiles, planned synthetic executions, usable-feedback floor, reserves, method, exposure order, cost range, and human respondents: 0.

The provisional audience:

- is run-local;
- expires no more than 30 days after materialization;
- is never registered;
- cannot be reused in another study;
- is never described as research-backed or observed human behavior.

Finalist approval still occurs later when the selected roster is ready.

## What the result means

The result is conditional on the audience description and modeled context you supplied. A single-profile run cannot support claims about differences across profiles or a broader audience. Missing applicable stability gates produce an exploratory or unresolved result rather than a manufactured shortlist.

Read [Synthetic evidence and validity](../concepts/synthetic-evidence-and-validity.md).

## How to make it reusable later

Start a new [research-backed panel build](build-an-audience-panel.md). The provisional package is not promoted, registered, or silently treated as evidence. The researched panel receives its own sources, review, audit, approval, and version.

## Next step

Continue to [Test ads](test-ads.md) and approve the frozen run plan before synthetic collection begins.
