# How Ad Testing Lab works

Ad Testing Lab is a four-capability workflow. You can use one capability by itself, but the handoffs keep private data, audience construction, creative testing, and real-world outcomes from being mixed together.

## The lifecycle

```text
Optional private data
        ↓
Audience Data Lab → approved aggregate evidence
        ↓
Audience Panel Builder → reusable audience package
        ↓
Ad Testing Lab → synthetic screening + dashboard
        ↓
Real-World Outcome Data Prep → preregistration or aggregate result import
        ↓
Separate Real-World Outcome Validation
        ↓ optional after repeated authenticated misses
Experimental Real-World Panel Calibration → bounded candidate → fresh validation → approval
```

## 1. Prepare private evidence only when needed

[Audience Data Lab](../skills/audience-data-lab/README.md) is the sole route for permissioned row-level CRM, customer, sales, product-usage, or performance data. It processes rows locally and releases only approved, privacy-reviewed aggregate evidence.

Skip this step when you have no private data or when public research is sufficient.

## 2. Choose an audience route

### Reuse a saved panel

Use an approved immutable panel package when its audience scope still matches the new study. Ad Testing Lab copies a hash-bound snapshot into the run; it does not edit the saved panel.

### Build a research-backed panel

[Audience Panel Builder](../skills/audience-panel-builder/README.md) collects and evaluates evidence, constructs grounded profiles, renders a human-readable review, performs a blind construction audit, and packages the approved result for reuse.

### Start without research

When exact creatives are ready but audience research is not, Ad Testing Lab can create the smallest honest run-local audience from the user’s description. Unsupported details stay `unknown`; the material expires within 30 days and cannot be registered or reused. The frozen run plan—not an empty research brief—is the approval surface.

Read [Build an audience without research](guides/build-an-audience-without-research.md).

## 3. Freeze the creative test

[Ad Testing Lab](../skills/audience-ad-testing-lab/README.md) requires exact finished creatives. It hashes the creative inputs, locks the audience scope, selects the method, calculates profile-aware synthetic capacity, reserves later stages, and presents the study plan before dispatch.

- Two to six creatives use complete exposure: each synthetic execution reviews the full set.
- Seven to 100 creatives use partial exposure after a burden pilot: each execution reviews a balanced subset.

The method can refuse to proceed when the design is too weak under the authorized capacity. Read [Methods and capacity](reference/methods-and-capacity.md).

## 4. Collect synthetic feedback

Fresh isolated contexts review the ads using a frozen protocol. These are synthetic executions, not human participants. Prompts collect structured reactions; deterministic tools calculate rankings, stability, coverage, and shortlist status.

The system keeps accepted responses, rejected attempts, raw provider returns, and dispatch records distinct. The dashboard exposes those records under Downloads.

## 5. Review the decision package

The HTML dashboard leads with the marketer’s decision: what was tested, what stood out, what remains uncertain, what moved forward after approval, and why. Methodology and exact denominators remain available without dominating the main view.

For imagery studies, attention overlays are created or imported after finalist approval. They diagnose visual alignment; they do not alter the synthetic ranking.

## 6. Learn from real campaign outcomes

[Real-World Outcome Data Prep](../skills/real-world-outcome-data-prep/README.md) can freeze a study before launch or import permissioned aggregate platform exports afterward. It preserves chronology, source bytes, provenance, and readiness but does not judge whether the panel was correct.

A separate Real-World Outcome Validation step can compare a preregistered synthetic ordering with eligible held-out aggregate outcomes. After repeated disjoint authenticated misses, Experimental Real-World Panel Calibration in Audience Panel Builder can propose one bounded persona-behavior update, preserve a complete new candidate and exact diff, and test that candidate on fresh held-out outcomes. Registration requires every evidence gate and explicit human approval. It creates a new version and cannot rewrite or silently activate the original panel.

## What never crosses the boundaries

- Raw private rows never enter audience panels, ad-testing prompts, dashboards, or the repository.
- Audience construction never changes the ad-review questionnaire or scoring.
- Synthetic feedback never becomes survey or market evidence.
- Outcome preparation never evaluates or calibrates the panel.
- Real-world validation never rewrites past predictions, responses, or packages.
- Experimental panel calibration never learns and evaluates from the same study or bypasses human approval.

## Next steps

- [Build an audience panel](guides/build-an-audience-panel.md)
- [Test ads](guides/test-ads.md)
- [Understand synthetic evidence and validity](concepts/synthetic-evidence-and-validity.md)
- [Understand calibration and real-world validation](concepts/calibration-and-real-world-validation.md)
- [Review every output and file](reference/outputs-and-files.md)
