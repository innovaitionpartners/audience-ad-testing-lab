# Route Workflows And Output Templates

## Contents

1. Shared response sequence
2. Create route
3. Authorized-audience import route
4. Refresh route
5. Augment route
6. Audit route
7. Provisional route
8. Response templates
9. Validation loop
10. Final deliverable templates

## Shared Response Sequence

Use separate responses so each approval point is the only deliverable in that
turn:

1. **Response 1:** intake, capability inventory, source plan, gaps, and questions.
2. **Response 2:** evidence synthesis and marketer-readable research brief.
3. **Response 3:** complete panel review, exact review-manifest binding, real
   report, and blind construction audit for `panel_construction` approval.
4. **Response 4:** package-proposal digest for `package_registration`
   approval. No reusable package is retained.
5. **Response 5:** canonical package and registration confirmation after the
   exact proposal hash is approved.

Audit stops after Response 2. Dogfood exits after report and audit. Provisional
materialization is an internal run-local helper, not a user-facing response
sequence. When creatives are supplied, Ad Testing Lab owns the user-facing
route and its run-plan approval. The helper produces no Response 1 through 5:
there is no research-plan approval, no research-brief approval, and no
panel-package approval. It cannot call either canonical package entry point.

The reusable-panel sequence is:

```text
synthesize evidence
→ approve `evidence_synthesis`
→ construct panel
→ render real report
→ blind construction audit
→ approve `panel_construction`
→ calculate package proposal digest without materializing a reusable package
→ approve `package_registration`
→ build the canonical package
→ register
```

The panel review manifest binds the canonical panel, complete Markdown
projection, complete HTML projection, and named review revision. The report
uses that exact dogfood or approved review-state snapshot and includes the
panel review manifest as an exact input. The audit is created only after that
report exists and binds the exact report manifest, which therefore binds the
review manifest and both human review surfaces.
The report's `workflow-state.json` is the pre-audit review snapshot (W0).
The later approved workflow-state snapshot (W1) records
`report_inputs_sha256`, `audit_sha256`, and then the exact proposed
`package_sha256`. W1 does not replace W0 inside the already sealed report:
W1 binds the report inputs and audit, while the audit binds the report manifest
and that manifest binds W0. This is a forward-only hash chain, not a circular
binding.
Changing one canonical input invalidates report reuse, audit reuse, package
build, and registration until every downstream surface is regenerated and
reapproved.

Proposal, build, and registration each revalidate the exact ledger, finding
support, synthesis matrix, report manifest, audit, and workflow state. They
require current `evidence_synthesis` and `panel_construction` approvals plus
the exact non-null report-input and audit bindings. Build and registration
also require the exact package binding and `package_registration` approval.
Registration reads the package once, validates that immutable byte snapshot,
and derives the brief and panel from it before any library mutation.

The route enum is closed to these six values:

```text
create_research_backed_panel
import_authorized_audience
refresh_existing_panel
augment_existing_panel
audit_existing_panel
provisional_immediate_panel
```

`migrate-audience-v2-to-v3.py` is an explicit maintenance command, not a
seventh workflow route.

This enum is executable, not descriptive. Research-intake validation accepts
all six values and rejects anything else. For
`import_authorized_audience`, source planning selects no public or
first-party source family and emits exactly one unresolved first-party
handoff requirement for Audience Data Lab to satisfy.

## Create Route

Use `create_research_backed_panel` when no approved reusable panel exists.

1. Plan a complete research run and an explicit population-frame request.
   Use the supported public adapters, or an already approved aggregate handoff
   from Audience Data Lab; never silently mix source universes.
2. Collect, normalize, score, and ledger the evidence.
3. Build the population frame. Preserve BLS OEWS persons, Census SUSB firms,
   Census CBP establishments, and every other distinct unit/denominator in
   separate partitions. Record missing critical joints explicitly.
4. Produce and validate the synthesis matrix.
5. Present the research brief for approval.
6. Construct only evidence-supported structural groups, overlays, and explicit
   profile combinations from the approved brief.
7. Build and validate the composition plan and final validity profile.
8. Render the real report and run the blind construction audit.
9. Approve the exact audited panel under `panel_construction`.

For Release B1 v3 work, stop after step 9 with the v3 brief, v3 panel,
population frame, composition plan, validity profile, report, and audit.
There is no v3 package, library resolution, quota/job allocation, profile
assignment, test result, score, rank, or dashboard in this release. The
package-proposal, build, registration, and library steps below remain the
unchanged v2 flow.

For Release B2, an already approved and packaged v3 panel may continue into
library resolution, frozen stage-roster allocation, profile binding, testing,
and the v3 dashboard. This later flow does not reopen the approved panel
construction. Approved Tier 1 panels remain reusable and use directional
planning allocation without the five-percentage-point structural-frame gate;
only provisional panels remain run-local.

At any distorted selected-for-dispatch roster, stop and present these choices:

1. increase synthetic capacity and regenerate the affected roster;
2. approve an explicit scope merge or exclusion, then rebuild the relevant
   package and roster; or
3. explicitly continue with a Tier 1 directional creative hypothesis stress
   test.

Never select a route for the user. A directional continuation changes the run
claim only; it does not silently downgrade or mutate the approved reusable
panel tier. Report both the full reserve and the validated selected subset,
and label only the selected-for-dispatch diagnostics as the run-claim
authority.

Deliverables: source plan, evidence-derived research report, research brief,
synthesis matrix, panel summary, validation report, and canonical panel. A v2
route may additionally deliver its approved package and registration record;
a Release B1 v3 route may not. The research report is generated only after the
evidence ledger, finding support, synthesis matrix, source scoring, coverage,
workflow state, source inventory, and excerpt inventory validate; a
brief/panel pair is not a research-report substitute.

## Held-Out Ordering Validation Route (Release C1)

Use this read-only route after a real-world test can be bound to a frozen panel
ordering. It never changes the panel, profiles, weights, prompts, synthetic
scores, or Ad Testing aggregation.

1. Freeze the exact panel result.
2. Register the real-world test before outcomes are accessed.
3. Collect permissioned aggregate outcomes only.
4. Compare the frozen and observed rankings under the registered scope.
5. Issue a narrow active claim or an honest negative, limited, or invalid result.
6. Render the self-contained plain-language report; keep hashes and diagnostic
   statuses in its technical audit trail.

Use the live runtime-pinned authority registry for every family, evaluation,
package, library, report, and dashboard operation. C1 v1 permits one final
analysis only. A campaign/time batch is indivisible between fitting and
holdout. Registered thresholds may tighten, never weaken, the product gates.
The package's audit observation set must exactly equal the observations
embedded in the evaluated comparisons.

The result does not predict click-through rate, conversion rate, revenue,
winning probability, or causal lift, and it does not replace live testing.
An optional dashboard section resolves the exact claim against the
authoritative validation library at render time. Only a currently active
claim from an authenticated validation package creates an active badge; a
panel's `tier_4` enum alone never does. Honest negative, limited, invalid,
expired, superseded, withdrawn, invalidated, and not-yet-active states render
as non-active results.

Registering a second same-scope claim does not silently make it current. The
earliest active claim remains current until an append-only authenticated
supersession event identifies its replacement. Active displays must match the
registered package and manifest hashes exactly.

## Authorized-Audience Import Route

Use `import_authorized_audience` when the user supplies arbitrary authorized
CSV, XLSX, JSON, or linked files rather than an approved aggregate handoff.

1. Send every arbitrary authorized file to Audience Data Lab.
2. Require its privacy profile and route decision before mapping. Direct
   identifiers or person-level data must remain there and require private
   aggregation; Panel Builder never opens those rows.
3. Require the exact mapping approval and deterministic transformation report.
4. Accept only the resulting validated `authorized-audience-handoff-v1`.
5. Bind each structural, psychographic/affinity, topic-bound social, and
   aggregate outcome document to its explicit route. Never infer a source
   shape or silently repair a lossy transform.
   Every transformed social observation ID used by an overlay must also
   resolve through a supported profile to the exact canonical cohort frame
   cell; merely generating a social document is not integration.
6. Build the authorized cohort frame from its exact unit, denominator,
   selection statement, coverage statement, and permitted uses.
7. Apply only predeclared calibration factors. Exactly `3.0` is allowed;
   anything above `3.0` stops the route.
8. Continue through the same brief, composition, validity, report, and audit
   approvals as the create route.

Equivalent flat, wide, nested, and linked inputs must converge to identical
canonical frame/evidence/outcome documents. Only source-profile and
transformation provenance may differ. A complete authorized frame may support
Tier 3; missing critical structure, modeled share above `0.30`, or another
declared downgrade condition yields Tier 1 with exact reason codes.

## Refresh Route

Use `refresh_existing_panel` when evidence age, scope, a known trigger, or field
learning requires reconsidering the approved construction.

1. Resolve the exact existing package and freeze its hashes.
2. Identify expired, changed, contradicted, or missing evidence and fields.
3. Research only the affected questions plus any required current-language lane.
4. Produce `panel-refresh-diff` with old value, proposed value, evidence, reason,
   and downstream effect.
5. Present the revised brief and diff for approval.
6. Create a new semantic version. Never overwrite the old package.

Deliverables: freshness audit, changed evidence, revised brief, refresh diff,
new panel version, package. Unchanged fields remain byte-equivalent where the
contract permits.

## Augment Route

Use `augment_existing_panel` when new evidence should be considered without
assuming the existing panel is stale.

1. Resolve and freeze the existing package.
2. Validate the new evidence and permission.
3. Show whether each item supports, qualifies, contradicts, or does not affect
   existing findings.
4. Produce `panel-augmentation-diff`.
5. Make no panel change unless the user approves the exact brief and diff.
6. Material changes create a new semantic version.

Deliverables: new-evidence audit, synthesis update, augmentation diff, optional
new panel version. “No change warranted” is a valid result.

## Audit Route

Use `audit_existing_panel` for a read-only review.

Check:

- package and manifest hashes;
- approval and scope;
- source permission and freshness;
- finding-to-item lineage;
- contradiction handling;
- segment sufficiency and combination support;
- weights versus planning allocations;
- privacy and sensitive-trait rules;
- calibration claims and refresh triggers.

Deliver `panel-audit-report` with `pass`, `needs_refresh`, or `incompatible`.
Do not rebuild, edit, version, package, or register anything. Offer the refresh
route separately when changes are needed.

## Provisional Route

Use `provisional_immediate_panel` only as Ad Testing Lab's internal run-local
helper. When creatives are supplied, Ad Testing Lab owns the user-facing
provisional route; do not send the user to Panel Builder for a second workflow
or approval sequence.

1. Accept the bounded audience scope, user-defined segments, and testing
   decision already collected for the creative run.
2. Set unsupported fields to `unknown`; do not invent research findings,
   evidence claims, profile detail, or audience prevalence.
3. Automatically set expiry from materialization time to no more than 30 days.
4. Materialize only the bounded run-local panel allowed by the shared v2
   immediate-run contract.
5. Return the run-local resolution to Ad Testing Lab for its run-plan approval.

There is no research-plan approval, no research-brief approval, and no
panel-package approval. Do not call the canonical proposal, build, or
registration commands. Keep the immediate-run package outside the canonical
reusable-package and library-registration flow; never register or reuse it,
weight it as audience share, or call it research-backed.

If no creatives are supplied, retain only a draft audience scope and do not
materialize a provisional panel. The user can later bring that scope and the
creatives to Ad Testing Lab, or choose the full reusable research-backed Panel
Builder route.

## Response Templates

### Response 1 — plan

```markdown
## Audience research plan ready for approval

- Route: [route]
- Audience and buying decision: [scope]
- Research tier: [tier]
- Evidence basis: [public, first-party, hybrid, or none]
- Existing panel: [ID/version/hash or none]
- Verified read capabilities: [capabilities]

| Research question | Evidence lane | Planned sources | Known gap |
|---|---|---|---|
| [question] | [lane] | [source families] | [gap/none] |

**Registry freshness:** [current/review due]
**Questions before research:** [questions/none]

Approve this plan before I collect or synthesize evidence.
```

### Response 2 — research brief

```markdown
## Audience research brief ready for approval

### What was researched
[scope, questions, methods, and evidence lanes]

### Strongest proof points
| Finding | Proof point and source | What complicates it | Confidence |
|---|---|---|---|
| [finding] | [linked source] | [qualification/contradiction] | [level + reason] |

### Proposed audience construction
| Segment or hypothesis | Why it exists | Evidence | Decision |
|---|---|---|---|
| [name] | [decision-relevant distinction] | [finding/evidence IDs] | [candidate/hypothesis/gap] |

### Coverage and gaps
[coverage table and consequences]

**Evidence review:** [evidence ledger, finding support, synthesis matrix, scored sources]
**Technical audit:** [source-inventory.json and verbatim-inventory.json after panel construction]

Approve or revise this exact brief. Panel construction does not begin in this response.
```

### Response 3 — panel construction review

```markdown
## Reusable audience panel ready for review

- Panel ID and version: [ID/version]
- Segments: [count]
- Buyer mindsets: [count]
- Buyer situations: [count]
- Explicit reusable profiles: [count]

### What changed
[create summary, refresh diff, augmentation diff, or provisional boundary]

### Files to open
1. [audience-panel-review.html]
2. [panel-summary.md]
3. [panel-review-manifest.json]
4. [audience-research-report.html]
5. [source-inventory.json]
6. [validation-report.md]
7. [synthesis-matrix.json — technical finding audit]
8. [saved-audience-panel.json]
9. [verbatim-inventory.json]
10. [construction-audit.json]

**Review revision:** [review-vN]
**Panel review manifest SHA-256:** [digest]
**Blind audit result:** [pass/fail]

Approve or revise only the canonical panel shown by this exact review
manifest. The `panel_construction` target is the SHA-256 of the exact canonical
review manifest, which binds the panel and both human review surfaces. Any
canonical-panel or human-review byte change requires a new revision, manifest,
report, audit, and approval request.
```

### Response 4 — package proposal

```markdown
## Audience panel package proposal ready for approval

- Panel ID and version: [ID/version]
- Approved review revision: [review-vN]
- Proposed package hash: [package SHA-256]

Approve this exact hash under `package_registration` before canonical package
bytes are built or registered.
```

### Response 5 — canonical build and registration

```markdown
## Audience panel registered

- Panel ID: [ID]
- Version: [version]
- Package hash: [hash]
- Research brief: [brief ID]
- Approved use: [use]
- Refresh trigger/date: [trigger/date]
- Package: [audience-panel-package.zip — transfer only]
```

## Validation Loop

Run each applicable command and fix every failure before the next approval:

```bash
python3 scripts/plan-research.py <intake.json> <new-plan.json> --capabilities <inventory.json>
python3 scripts/normalize-social-evidence.py <adapter> <input.json> <new-batch.json> [--mapping <mapping.json>]
# Optional upstream handoff gate: validate a supplied approved aggregate handoff before it enters the evidence ledger or synthesis.
python3 scripts/validate-data-handoff.py <handoff.json> --expected first_party|performance
python3 scripts/build-evidence-ledger.py <plan-id> <new-ledger.json> <batch...>
python3 scripts/score-research-sources.py <candidates.json> <new-scored.json>
python3 scripts/validate-finding-support.py <ledger.json> <finding-support.json>
python3 scripts/validate-synthesis-matrix.py <ledger.json> <finding-support.json> <synthesis-matrix.json>
# Approval: approve the exact evidence synthesis, then construct the panel.
python3 scripts/render-panel-review.py --brief <brief.json> --panel <panel.json> --review-revision <review-vN> --generated-at <timestamp> --output-dir <new-directory> [audit inputs]
python3 scripts/render-research-report.py --workflow-state <state.json> --brief <brief.json> --panel <panel.json> --plan <plan.json> --scored-sources <scored.json> --ledger <ledger.json> --finding-support <finding-support.json> --synthesis <synthesis.json> --panel-review-manifest <panel-review-manifest.json> --panel-summary <panel-summary.md> --panel-review-html <audience-panel-review.html> --generated-at <timestamp> --output-dir <new-directory>
python3 scripts/validate-panel-construction-audit.py --audit <audit.json> --brief <brief.json> --panel <panel.json> --ledger <ledger.json> --finding-support <finding-support.json> --synthesis <synthesis.json> --panel-review-manifest <panel-review-manifest.json> --report-manifest <report-manifest.json>
python3 scripts/propose-panel-package.py --workflow-state <state.json> --audit <audit.json> --brief <brief.json> --panel <panel.json> --ledger <ledger.json> --finding-support <finding-support.json> --synthesis <synthesis.json> --panel-review-manifest <panel-review-manifest.json> --report-manifest <report-manifest.json>
# Approval: record the exact proposed package hash in the workflow binding and package_registration approval.
python3 scripts/build-approved-panel-package.py --workflow-state <state.json> --audit <audit.json> --brief <brief.json> --panel <panel.json> --ledger <ledger.json> --finding-support <finding-support.json> --synthesis <synthesis.json> --panel-review-manifest <panel-review-manifest.json> --report-manifest <report-manifest.json> --output-dir <new-package-directory>
python3 scripts/register-approved-panel.py --workflow-state <state.json> --audit <audit.json> --ledger <ledger.json> --finding-support <finding-support.json> --synthesis <synthesis.json> --panel-review-manifest <panel-review-manifest.json> --report-manifest <report-manifest.json> --package <new-package-directory>/audience-panel-package.zip [--library-root <directory>]
```

Reconcile the exact brief, panel, source, synthesis, report, audit, and workflow
bindings. The exact operational order is:

1. Plan and normalize the research inputs. If an approved aggregate
   first-party or performance handoff is supplied, validate it through the
   optional upstream gate before it enters the evidence ledger or synthesis.
2. Build the ledger, score sources, validate finding support, and validate the
   synthesis before `evidence_synthesis` approval.
3. Construct the panel and render its complete Markdown/HTML review,
   validation report, review manifest, and manifest-bound approval request.
4. Render the evidence-derived report from the exact dogfood or approved
   review-state snapshot, supplying the exact review manifest and its two
   bound review outputs.
5. Bind the audit to that report manifest and validate it with the blind-audit
   command.
6. For canonical packaging, record the exact non-null report-input and audit
   hashes in the approved workflow state. Keep the exact current
   `evidence_synthesis` approval bound to the validated synthesis hash and the
   exact current `panel_construction` approval bound to the canonical panel-review manifest
   hash.
7. Calculate the proposal without retaining reusable package bytes.
8. Record the proposed package hash in `bindings.package_sha256` and approve
   that same hash under `package_registration`.
9. Build into a new directory and register that exact ZIP. Supply the same
   canonical ledger, finding support, synthesis matrix, and report manifest to
   proposal, build, and registration; none of those boundaries assumes an
   earlier command already validated them.

Dogfood stops after step 5. It must not call proposal, canonical build, or
registration. Existing output paths are never overwritten.

## Final Deliverable Templates

### Refresh or augmentation diff

```markdown
| Field or finding | Existing | Proposed | Exact evidence | Reason | Downstream effect |
|---|---|---|---|---|---|
```

### Audit report

```markdown
# Panel Audit: [panel ID/version]

## Decision
[pass, needs_refresh, or incompatible]

## Package and approval integrity
[hash and approval results]

## Evidence and synthesis
[source freshness, lineage, contradictions, confidence]

## Construction
[segments, mindsets, situations, combinations, weights]

## Privacy and allowed use
[results]

## Required action
[none, refresh route, or stop]
```

### Panel summary

Show scope, segments, buyer mindsets, buyer situations, explicit profiles,
weighting rules, proof needs, evidence IDs, and experimental combinations.

### Validation report

Show route, tier, evidence basis, registry freshness, source decisions,
synthesis confidence, unresolved contradictions, evidence gaps, separate
validity states, package hashes, and errors.
