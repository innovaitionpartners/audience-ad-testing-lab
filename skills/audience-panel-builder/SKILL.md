---
name: audience-panel-builder
version: 1.0.0
description: Research, construct, validate, package, refresh, audit, or use reusable evidence-grounded audience panels for synthetic testing. Use when the user asks to build panelists, personas, audience segments, buyer mindsets, a reusable audience panel, a research-backed test audience, or an audience research report; when they want a panel to react to, review, or pressure-test ads, newsletter concepts, messages, content, or other creative; when they provide CRM aggregates, public surveys, social/community data, supplied Last30Days JSON, Apify exports, interviews, reviews, or performance data to improve a panel; or when a synthetic test needs a new or materially updated reusable audience package.
---

# Audience Panel Builder

Build and use reusable evidence-grounded audience models for synthetic testing.
When the request includes creative or concepts, use the approved panel profiles
to generate and aggregate synthetic reactions under the co-shipped Ad Testing
Lab response contracts. Do not stop at panel delivery or narrate a handoff.

## Internal Method Invariants

- Preserve the co-shipped creative-review questionnaire, exposure order, score
  fields, retest rules, and aggregation contracts while improving panel
  construction.
- Never treat post count, engagement, a synthetic profile count, or source count as audience prevalence.
- Never turn raw CRM rows, social handles, or named people into panelists.
- Never process row-level CRM or performance data here. Accept only an
  approved `authorized-audience-handoff-v1`,
  `audience-first-party-evidence-v1`, or
  `audience-performance-evidence-v1` handoff from Audience Data Lab.
- Preserve the existing saved-panel ontology and dynamic one-panelist-per-worker interview behavior.
- Treat an approved v2 package as a Tier 1 evidence-grounded panel. It supports a Directional creative hypothesis stress test.
- Release B1 builds the population frame, composition plan, validity profile,
  and v3 brief/panel documents. When synthetic reactions are requested,
  continue through the co-shipped B2 allocation and testing runtime without
  changing its quotas, jobs, assignments, questionnaire, scoring, or dashboard
  contracts.
- An approved v3 Tier 1 panel is reusable. Its planning allocations remain
  directional and do not use the five-percentage-point structural-frame gate.
  Only a provisional panel is run-local.
- Provisional panel materialization is an internal run-local helper. When
  creatives are supplied without research, continue through the co-shipped Ad
  Testing Lab testing runtime. Perform no empty research or package approval,
  set unsupported fields to `unknown`, set expiry to no more than 30 days, and
  never register or reuse the materialized panel.
- For a v3 run whose selected roster is distorted, present the three user
  decisions without choosing: increase synthetic capacity; approve an explicit
  scope merge or exclusion and rebuild; or explicitly continue as a Tier 1
  directional creative hypothesis stress test.
- Treat later aggregate outcome feedback as a separate read-only lane. It does not turn current synthetic results into observed customer behavior and cannot change scores, rankings, profiles, frame weights, or panel weights.
- Treat the Experimental Persona Behavior Calibration Sandbox as a
  fictional-fixture engineering test only. It may materialize one separately
  versioned sandbox candidate for one existing persona and one behavioral
  field, but it cannot create a reusable package, register or activate a
  panel, modify an active panel, or validate real-world accuracy.

## Routing

Read only what the current phase needs:

Send raw real-world advertising-platform exports to Real-World Outcome Data Prep.
Do not normalize raw real campaign exports here.

| Need | Read or run |
|---|---|
| Install numerical validation dependencies when absent | `python3 -m pip install -r ../audience-ad-testing-lab/requirements-screening.txt` |
| Source lanes, selection rules, Last30Days, or Apify | `references/source-strategy.md` |
| Intake, source-plan, social-batch, brief, panel, or package contracts | `references/contracts.md` |
| Exact research-brief and saved-panel allowlists | `references/v2-panel-contracts.md` |
| Curated social-export field mapping | `references/social-mapping-contract.md` |
| Question-level synthesis, conflicts, confidence, and segment sufficiency | `references/research-synthesis-method.md` |
| Research and panel-construction rules | `references/construction-method.md` |
| Generate synthetic reactions to ads, newsletter concepts, messages, content, or other creative | Read `../audience-ad-testing-lab/SKILL.md` and follow its response-collection, scoring, and dashboard contracts with the approved panel |
| V3 population, composition, validity, outcome-feedback binding, and calibration-proposal contracts | `references/v3-population-contracts.md` |
| Create, refresh, augment, audit, provisional, and approval-response templates | `references/route-workflows-and-output-templates.md` |
| Concrete plan, source, brief, profile, refresh, and audit examples | `references/worked-route-examples.md` |
| Plan a research run | `python3 scripts/plan-research.py <intake.json> <plan.json> --capabilities <inventory.json>` |
| Normalize Last30Days JSON | `python3 scripts/normalize-social-evidence.py last30days <input.json> <output.json>` |
| Normalize a curated or Apify export | `python3 scripts/normalize-social-evidence.py mapped <input.json> <output.json> --mapping <mapping.json>` |
| Validate an optional approved Data Lab handoff before evidence integration | `python3 scripts/validate-data-handoff.py <handoff.json> --expected first_party|performance` |
| Plan exact population sources | `python3 scripts/plan-population-sources.py --frame-request <request.json> --registry references/population-source-registry-v2.json --capabilities <inventory.json> --output <new-plan.json>` |
| Build a population frame and provisional validity profile | `python3 scripts/build-population-frame.py --frame-request <request.json> --observations <batch.json> [<batch.json> ...] --output <new-frame.json> --validity-output <new-validity.json>` |
| Build the explicit composition plan | `python3 scripts/build-panel-composition.py --population-frame <frame.json> --structural-findings <structural.json> --overlay-findings <overlays.json> --profile-specs <profiles.json> --requested-tier <tier> --evidence-basis <basis> --plan-id <id> --plan-version <version> --built-at <timestamp> --output <new-composition.json>` |
| Build the item-level evidence ledger | `python3 scripts/build-evidence-ledger.py <plan-id> <ledger.json> <normalized-batch...>` |
| Score and validate candidate sources | `python3 scripts/score-research-sources.py <candidates.json> <scored.json>` |
| Validate finding-to-item support | `python3 scripts/validate-finding-support.py <ledger.json> <finding-support.json>` |
| Validate integrated research synthesis | `python3 scripts/validate-synthesis-matrix.py <ledger.json> <finding-support.json> <synthesis-matrix.json>` |
| Render the complete panel review, manifest, approval request, and validation report | `python3 scripts/render-panel-review.py --brief <brief.json> --panel <panel.json> --output-dir <directory> [--review-revision review-vN] [--generated-at <timestamp>] [audit inputs] [--run-plan <manifest.json> --run-results <lineage-results.json>]` |
| Render the hash-bound evidence-derived report | `python3 scripts/render-research-report.py --workflow-state <state.json> --brief <brief.json> --panel <panel.json> --plan <plan.json> --scored-sources <scored.json> --ledger <ledger.json> --finding-support <finding-support.json> --synthesis <synthesis.json> --panel-review-manifest <panel-review-manifest.json> --panel-summary <panel-summary.md> --panel-review-html <audience-panel-review.html> --generated-at <timestamp> --output-dir <new-directory>` |
| Validate the blind construction audit | `python3 scripts/validate-panel-construction-audit.py --audit <audit.json> --brief <brief.json> --panel <panel.json> --ledger <ledger.json> --finding-support <finding-support.json> --synthesis <synthesis.json> --panel-review-manifest <panel-review-manifest.json> --report-manifest <report-manifest.json>` |
| Bind approved aggregate outcome feedback without changing the panel | `python3 scripts/bind-panel-outcome-feedback.py --panel <saved-audience-panel-v3.json> --feedback <panel-outcome-feedback-v1.json> --binding-id <id> --bound-at <timestamp> --output <new-binding.json>` |
| Operate the fictional-only persona behavior sandbox | `references/experimental-persona-behavior-calibration.md` |
| Freeze and generate the fictional sandbox study | `python3 scripts/build-synthetic-persona-behavior-study.py --manifest-output <new-manifest.json> --public-fixtures-root <new-public-root> --oracle-fixtures-root <new-oracle-root> --created-at <timestamp>` |
| Register creative attributes before outcome access | `python3 scripts/register-synthetic-creative-attributes.py --input <registration-input.json> --output <new-registry.json>` |
| Normalize one fictional platform export | `python3 scripts/import-synthetic-platform-outcomes.py --platform <meta|google|linkedin|tiktok> --input <raw-export.json> --source-sha256 <digest> --study-manifest <manifest.json> --attribute-registry <registry.json> --output <new-observations.json>` |
| Manage the append-only synthetic evidence library | `python3 scripts/manage-synthetic-outcome-evidence-library.py <init|append|correct|list|show|verify> [command arguments]` |
| Diagnose one frozen synthetic evidence projection | `python3 scripts/diagnose-experimental-persona-behavior.py --input <diagnosis-input.json> --output <new-diagnosis.json>` |
| Seal one bounded synthetic behavior proposal | `python3 scripts/propose-experimental-persona-behavior-update.py --input <proposal-input.json> --output <new-proposal.json>` |
| Materialize one sandbox-only persona candidate | `python3 scripts/materialize-experimental-persona-candidate.py --base-panel <panel.json> --proposal <proposal.json> [frozen evidence inputs] --candidate-id <id> --candidate-version <newer-version> --created-at <timestamp> --output-dir <new-directory>` |
| Exercise the base and sealed candidate through the registered private stage | `python3 scripts/run-synthetic-persona-behavior-exercise.py --study-manifest <manifest.json> --public-scenarios-root <public-root> --creative-attribute-registry <registry.json> --base-panel <panel.json> --candidate-bindings-and-panels <sealed-envelope.json> --exercise-id <id> --exercised-at <timestamp> --output <new-exercise.json>` |
| Evaluate sealed results against separately held hidden truth | `python3 scripts/evaluate-synthetic-persona-behavior-proposal.py --study-manifest <manifest.json> --observations <observations.json> --exercise <exercise.json> --oracles <oracles.json> --diagnoses <diagnoses.json> --proposals <proposals.json> --candidates <candidates.json> --phase-receipts <receipts.json> --evaluated-at <timestamp> --output <new-evaluation.json>` |
| Render the static fictional-only sandbox report | `python3 scripts/render-experimental-persona-behavior-report.py --evaluation <evaluation.json> --proposals <proposals.json> --candidates <candidates.json> --template assets/experimental-persona-behavior-report-template.html --output <new-report.html>` |
| Preregister one frozen Tier 4 validation design | `python3 scripts/register-panel-validation.py --input <draft.json> --output <new-registration.json> --authority-root <authority-dir> --authority-index <authority-index.json> --authority-registry <trusted-registry.json> --authority-secret-file <owner-only.key>` |
| Freeze the complete Tier 4 claim family | `python3 scripts/build-panel-claim-family.py --family-input <family-input.json> --output <new-family.json> --authority-registry <trusted-registry.json> --authority-secret-file <owner-only.key>` |
| Evaluate held-out outcomes and issue only a supported claim | `python3 scripts/evaluate-panel-outcomes.py --registration <registration.json> --comparison <comparison.json> [--comparison <comparison.json> ...] --claim-family <family.json> --evaluated-at <timestamp> --evaluation-output <new-evaluation.json> [--claim-output <new-claim.json> --claim-expires-at <timestamp>] --authority-root <authority-dir> --authority-index <authority-index.json> --authority-registry <trusted-registry.json> --authority-secret-file <owner-only.key>` |
| Build the immutable Tier 4 evidence package | `python3 scripts/build-panel-validation-package.py --inputs-dir <validated-run> --panel-package <panel-package.zip> --output-dir <new-directory> --authority-registry <trusted-registry.json> --authority-secret-file <owner-only.key>` |
| Register or inspect Tier 4 claim lifecycle state | `python3 scripts/manage-panel-validation-library.py --authority-registry <trusted-registry.json> --authority-secret-file <owner-only.key> register <validation-package.zip> --library-root <validation-library> --registered-at <timestamp>` |
| Render a Tier 4 validation report | `python3 scripts/render-panel-validation-report.py --registration <registration.json> --evaluation <evaluation.json> [--claim <claim.json> --library-root <validation-library> --validation-package <registered-package.zip> --as-of <timestamp>] --authority-registry <trusted-registry.json> --authority-secret-file <owner-only.key> --template assets/panel-validation-report-template.html --output <new-report.html>` |
| Calculate the canonical package proposal | `python3 scripts/propose-panel-package.py --workflow-state <state.json> --audit <audit.json> --brief <brief.json> --panel <panel.json> --ledger <ledger.json> --finding-support <finding-support.json> --synthesis <synthesis.json> --panel-review-manifest <panel-review-manifest.json> --report-manifest <report-manifest.json>` |
| Build the proposal-approved package | `python3 scripts/build-approved-panel-package.py --workflow-state <state.json> --audit <audit.json> --brief <brief.json> --panel <panel.json> --ledger <ledger.json> --finding-support <finding-support.json> --synthesis <synthesis.json> --panel-review-manifest <panel-review-manifest.json> --report-manifest <report-manifest.json> --output-dir <new-directory>` |
| Register the exact approved package | `python3 scripts/register-approved-panel.py --workflow-state <state.json> --audit <audit.json> --ledger <ledger.json> --finding-support <finding-support.json> --synthesis <synthesis.json> --panel-review-manifest <panel-review-manifest.json> --report-manifest <report-manifest.json> --package <package.zip> [--library-root <directory>]` |

The approval-gated package and registration commands call the unchanged co-shipped Ad Testing Lab v2 generator and library. Install the complete plugin when using Panel Builder.

## Workflow

### 1. Lock The Audience Decision

Select the workflow route:

```text
create_research_backed_panel
import_authorized_audience
refresh_existing_panel
augment_existing_panel
audit_existing_panel
provisional_immediate_panel
```

These are the six workflow routes. V2-to-v3 migration is an explicit
maintenance command, not a seventh route. `create_research_backed_panel` may
use supported public population adapters or an already approved aggregate
handoff. Use `import_authorized_audience` when the user supplies arbitrary
authorized files: send those files to Audience Data Lab for privacy profiling,
mapping approval, and deterministic transformation, then accept only its
approved aggregate handoff here. Never read or normalize arbitrary private
files directly in Panel Builder.

The intake validator and source planner execute this same closed six-value
enum. An authorized import produces no direct source selection: it produces
one unresolved first-party handoff requirement until Audience Data Lab
returns the validated aggregate handoff. Any seventh or unknown route fails
at intake.

Then collect the audience, category, market, geography, buying context, exclusions, decision, research tier, approved evidence handoffs, and requested connector hints. Keep route, tier, and evidence basis separate. Do not start with persona prose.

Inventory actual read capabilities in `connector-capability-inventory-v1`. A provider name is a hint, not proof of listening access. Create `audience-panel-research-intake-v1` and run `plan-research.py` with the inventory. The plan selects source families and collection requirements from the registry. Registry entries are templates, not evidence.

Follow the route-specific workflow and staged responses in `references/route-workflows-and-output-templates.md`. For reusable research-backed routes, Response 1 presents the plan and stops for approval before collection. The provisional helper is the explicit exception: it does not inherit the reusable research or package approval sequence.

The canonical reusable-panel sequence is exact:

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

When the request also supplies creative or concepts to evaluate, do not stop at
panel registration or delivery. Continue with the co-shipped review workflow
using the exact approved panel. Treat comparable text-only newsletter concepts
or messages as `copy_only` creative variations unless the user supplies another
supported representation.

Dogfood exits after report and audit. Provisional work cannot call either canonical package entry point. It has no research-plan approval, no research-brief approval, and no panel-package approval.

The report binds the exact review-state snapshot. The blind audit then binds
that report manifest. After the audit passes, record its hash in the approved
workflow state before calculating the package proposal. Record the proposal
hash in both `bindings.package_sha256` and the exact
`package_registration` approval before build or registration. If any canonical
brief, panel, evidence, synthesis, report, audit, or package byte changes,
discard downstream approvals and derived outputs and restart from the first
stale binding.

Proposal, build, and registration each receive the exact ledger, finding
support, synthesis matrix, and report manifest. Each command independently
revalidates those documents, recomputes the audit chain, requires current
`evidence_synthesis` and `panel_construction` approvals, and matches the exact
non-null report-input and audit bindings. Build and registration also require
the exact package binding and `package_registration` approval. Registration
derives its brief and panel from one immutable validated package-byte snapshot
before any library write.

### 2. Build A Mixed Evidence Base

Use the separate evidence lanes defined in `references/source-strategy.md`:

1. structural;
2. recurring surveys and professional research;
3. social, community, forum, and review evidence;
4. approved aggregate first-party research and owned-social evidence;
5. approved aggregate historical performance context.

Choose the best authorized corpus for each research question. An authenticated connector does not automatically outrank native research. Use only verified read capabilities. Otherwise use the runtime's web, browser, public API, and search capabilities for relevant public discussion. Do not invoke a different research skill for public collection; the `import_authorized_audience` handoff to Audience Data Lab is the explicit privacy exception. Import Last30Days only when the user or outer orchestrator has supplied its stable versioned agent JSON export. Use a mapped permitted export for Apify or another collector. Normalize every route before synthesis.

For structural population evidence, preserve source universes exactly. BLS
OEWS person estimates, Census SUSB employer-firm estimates, and Census CBP
establishment estimates remain separate unit/denominator partitions. Do not
combine them into a universal audience denominator. Public proxy frames must
name the proxy boundary and every missing critical joint. Authorized cohort
frames must bind the exact Data Lab handoff, denominator, selection statement,
coverage statement, and any approved calibration factor.

If an approved first-party or performance handoff is supplied, validate it as
an optional upstream input before it enters the evidence ledger or synthesis.
The operational order is research plan, normalization, optional handoff
validation, evidence ledger, source scoring, finding support, synthesis,
evidence approval, panel construction, panel review, evidence-derived report,
blind audit, proposal, package approval, build, and registration.

Treat source roles separately. Structural and survey sources may support composition or population framing when their methods allow it. Social evidence supports language, current context, hypotheses, objections, proof demands, peer signals, and channel norms.

### 3. Score And Curate Sources

Create `audience-source-candidates-v1`, run `score-research-sources.py`, and review the resulting acceptance decisions. Permission failures are hard stops. Weak directness or opaque methodology remains visible and cannot be repaired by adding more weak sources.

Every accepted social source must retain its platform, query, collection window, unit of analysis, access route, deduplication control, bot/spam control, engagement availability, and exact evidence-item IDs.

Build `audience-evidence-ledger-v1` before findings. Keep normalized items separate from interpretation. Track upstream-source IDs so reposts and articles quoting the same study do not count as independent corroboration.

### 4. Produce The Research Brief

Create `audience-finding-support-v1`, binding every proposed finding to exact ledger item IDs. Then apply `references/research-synthesis-method.md` question by question. Put supporting, qualifying, and contradicting items into `audience-synthesis-matrix-v1`; classify convergence, complementarity, mixed evidence, discordance, or single-source support; assess methodological limitations, relevance, coherence, and adequacy; and retain negative cases.

When isolated workers are supported, dispatch `agents/evidence-synthesis-worker.md` with only the worker prompt, complete synthesis method, plan, scored sources, ledger, finding support, and approved aggregate handoffs. It must not see candidate creative. In a shared-context-only runtime, run the same procedure sequentially here and mark the limitation.

Validate the synthesis matrix before drafting the strict `audience-research-brief-v2` contract. Every brief finding cites evidence IDs, states confidence and its inference boundary, and names the marketer and creative consequence without expanding the claim. A segment candidate must pass every sufficiency gate. Otherwise merge it, keep it as an emerging hypothesis, mark the combination experimental, or exclude it.

Broad cross-audience findings may supply shared background context, but they
cannot be the sole support for materially distinct archetypes. Give each
archetype at least one profile-specific finding, or narrower cohort findings
whose selected sets actually distinguish the profiles. When the available
research is genuinely omnibus, declare each finding's scope in its existing
inference boundary as `Evidence scope: cross-audience.`, `Evidence scope:
cohort:<segment-id>.`, or `Evidence scope: profile:<archetype-id>.` Scope is
never inferred from which archetype happens to cite a finding. An affected
archetype may use the existing inference-boundary field for the exact structured
exception `Evidence-specificity exception [unsupported_distinction=<what is not
supported>; missing_research=<what evidence is absent>; bounded_use=<what
limited use remains>]: <justification>`. Identical finding sets across distinct
archetypes without valid structured exceptions fail profile traceability.

Present the complete marketer-readable research brief at the approval gate:

- what was researched;
- strongest proof points and source links;
- what the evidence says about the audience;
- proposed segments and why each exists;
- coverage and gaps;
- which evidence influenced construction and which did not.

Do not make the JSON file or ZIP the primary review surface.
Stop here in Response 2. Do not construct panelists until the user approves this exact brief.

### 5. Build The Panel After Approval

Only after the exact brief is approved, construct `saved-audience-panel-v2` using `references/construction-method.md`.

Compile each profile from separate structural, operating, decision, media, social/community, first-party, and performance layers. Do not create an implicit cross-product of title, industry, company size, mindset, and buying situation. Every explicit combination needs resolving evidence or an `experimental` label.

This creates reusable grounded profiles. Ad Testing Lab later instantiates run-specific synthetic panelists from them and performs the existing progressive dynamic interviews. Do not move response prompts or ad-conditioned behavior into the saved panel.

Run the deterministic validators against the exact approved brief. Render the
Markdown and HTML review plus `panel-review-manifest.json` before the research
report. Include that exact review manifest as a bound input to the research
report. When isolated workers are supported, dispatch
`agents/panel-construction-auditor.md` with only the worker prompt and these
seven approved canonical documents: approved brief, saved panel, ledger,
finding support, synthesis matrix, panel-review manifest, and research-report
manifest. Do not pass construction rules, v2 allowlists, candidate creative,
evaluation/performance output, campaign outcomes, winner labels, or private
reasoning. Fix unsupported construction by removing or relabeling it, never by
inventing evidence.

Use a validate-fix-revalidate loop until the exact brief, synthesis, panel, readable reports, and construction audit all agree. Approve `panel_construction` only for the SHA-256 of the exact canonical `panel-review-manifest.json`; that manifest binds the audited panel and both human review surfaces. Never overwrite an existing output path.

### 6. Package And Deliver

First deliver the real report and construction-audit surfaces:

1. `audience-research-report.html` — primary human review;
2. `source-inventory.json` and `verbatim-inventory.json` — source audit;
3. `audience-panel-review.html` — primary marketer-readable panel review;
4. `panel-summary.md` — complete Markdown projection of the same canonical fields;
5. `panel-review-manifest.json` — exact canonical JSON/Markdown/HTML binding and review revision;
6. `panel-construction-approval-request.md` — request bound to the exact review manifest;
7. `validation-report.md` — route, tier, evidence basis, gaps, and validity state;
8. `construction-audit.json` — blind result whose report manifest binds the exact panel-review manifest;
9. `synthesis-matrix.json` — technical finding-level audit when research was run;
10. `saved-audience-panel.json` — canonical machine-readable panel.

Treat `saved-audience-panel.json` as canonical. Never hand-edit either human
projection. Rerun the renderer into a new review revision. The HTML and
Markdown must expose every marketer-relevant canonical field, including empty
or unknown states, without inventing research for provisional panels. The HTML
uses the Lab dashboard design system and readable body type. Both projections
must also list every approved research source with a direct URL and its full
audit metadata; no-research panels render an honest empty source directory.

After the audit passes and the exact panel receives `panel_construction`
approval, calculate the package proposal digest. This temporary calculation
must not retain or expose reusable package bytes. Present the exact proposal
hash for `package_registration` approval.

Only after that exact hash is approved, build the canonical package into a new
directory with `build-approved-panel-package.py`, then register the exact ZIP
with `register-approved-panel.py`. Deliver the resulting
`audience-panel-package.zip` as a secondary transfer/import file.

Say explicitly that the ZIP is for transfer and registration, not for previewing the panel. Never give the ZIP as the only deliverable.

Ad Testing Lab consumes the registered version or portable ZIP and must not rebuild the research.

### 7. Bind Later Outcome Feedback Read-Only

After a real study produces permissioned aggregate outcomes, validate and bind
each `panel-outcome-feedback-v1` document to the exact
`saved-audience-panel-v3`. Keep outcome feedback outside the blind construction
audit and current synthetic test results. A binding records what was observed
for the exact study, variants, cohorts, metrics, sources, windows, and holdout
design; it does not make the synthetic panel responses observed customer
behavior.

If the feedback warrants further work, create a separate non-executable
calibration-refresh proposal. Its status remains
`requires_calibration_approval`, its diff contains no operations, and it does
not change the current panel or feedback binding. Only a separately approved,
versioned refresh may later specify panel changes.

### 7a. Publish Held-Out Ordering Validation (Release C1)

Use the narrow Tier 4 route only after a frozen panel ordering can be checked
against permissioned aggregate real-world outcomes. The plain-language flow is:

1. freeze the panel result;
2. register the real-world test;
3. collect aggregate outcomes;
4. compare the rankings; and
5. issue a narrow claim or an honest negative result.

Render the marketer-first validation report from the authenticated registration,
evaluation, and optional active claim. It must show exact scope, expiry,
limits, and technical hashes. Tier 4 does not predict click-through rate,
conversion rate, revenue, winning probability, or causal lift; it does not
replace live testing. Never add a Tier 4 dashboard badge from a panel enum
alone: an exact active claim in an authenticated validation package plus its
authoritative current library lifecycle is required. Without the library
lookup, an issued claim is shown as unregistered, never active.

Treat the validation archive and its private registry as a separate immutable
lineage from the reusable panel package. Register only aggregate-only validation
packages whose exact base-panel binding revalidates. Lifecycle changes are
append-only `expired`, `superseded`, `withdrawn`, or `invalidated` events; never
rewrite a claim or its package. A current claim lookup must reauthenticate the
stored package and apply expiry and lifecycle state as of the requested time.
Negative, limited, invalid, expired, superseded, withdrawn, invalidated, and
not-yet-active results remain visible as non-active outcomes.

Every family build, evaluation, package build/validation, library operation,
report, and dashboard Tier 4 display requires the live runtime-pinned authority
registry and protected secret. A serialized self-hash never substitutes for
authority. V1 permits one final analysis only; reject interim looks. Treat a
campaign or time batch as indivisible across fitting and holdout, honor the
stricter of registered and built-in eligibility thresholds, and package exactly
the observations embedded in the evaluated comparisons.

Package publication stages and authenticates the complete directory before one
atomic publish. Library registration validates one private byte snapshot and
can recover an exact orphan package after an interrupted index commit. Library
writes use an operating-system file lock; never reclaim a lock by unlinking it.
Events must be appended in strictly increasing effective-time order. A later
same-scope registration remains inactive for current-selection purposes until
an explicit authenticated supersession event names it.

Release C1 ends at evaluation, narrow reporting, optional behavior-neutral
display, and claim lifecycle governance. Do not use validation outcomes to
modify panel construction, profiles, weights, allocations, prompts, responses,
scores, aggregation, or dispatch. Those are not C1 behaviors.

### 7b. Run The Experimental Persona Behavior Calibration Sandbox

This sandbox uses fictional synthetic fixtures to propose and materialize a draft update to one existing persona. It does not validate real-world accuracy, cannot create a reusable package, and cannot register or activate a panel.

Use this route only to verify the fictional synthetic-fixture machinery in
`references/experimental-persona-behavior-calibration.md`.

> Built and evaluated with fictional synthetic fixtures only. This output does not validate real-world panel accuracy, does not prove that the proposed change will improve outcomes, and cannot modify an active panel.

Freeze the study manifest and creative-attribute hypotheses before outcomes.
Keep public engine inputs physically separate from hidden truth. Run
diagnosis, proposal, candidate materialization, and base-versus-candidate
exercise only through their completed registered private-stage roles. The
engine's admitted source, inputs, environment, and readable filesystem must
exclude oracle code and hidden truth. Reveal the sealed scenarios and oracle
documents only to the separate evaluator after the proposal and candidate are
sealed.

The only materializable operation is one `profile_snapshot_update` against one
existing persona and exactly one of `anxieties`, `decision_context`,
`motivations`, `proof_needs`, or `role_context`. Preserve the population
frame, segment membership, composition, profile and overlay allocations,
capacity, one-worker-per-panelist execution, prompts, responses, scoring,
aggregation, every unrelated persona, the base panel, and every production
library byte and active pointer.

A sandbox candidate is a complete standalone panel for fictional exercise,
not a reusable package. Standard registration and activation must reject it.
No-change, abstain, insufficient, and invalid results create no candidate.
Longitudinal creative-feature evidence is an association, not a causal,
preference, or persona-fact claim.

### 8. Hand Off V3 Allocation Claims

When a Release B2 Ad Testing Lab run consumes an approved v3 package, keep the
reusable panel claim separate from the run allocation claim. Full screening,
boundary-reserve, and finalist-reserve rosters describe frozen capacity.
Only the validated selected-for-dispatch subset in a successful, manifest-bound
job envelope describes the realized stage roster.

Bind every accepted response to its exact validated job before reporting stage
results: screening and finalist use their stage envelopes, while boundary uses
the ordered union of newly authorized jobs from all validated waves. The latest
cumulative boundary subset remains the allocation-claim authority. If a
dispatch audit exists, reconcile every bound job as accepted or exhausted.

If the selected subset is frame-aligned, it may use the approved structural
frame claim. If it is distorted, ask the user to increase capacity, approve a
scope merge or exclusion and rebuild, or explicitly continue directionally.
Never make that decision for the user. A directional continuation changes the
run claim, not the approved reusable tier of the saved panel.

Describe the five-percentage-point test only as the product allocation threshold, never a
margin of error. Do not present target weights, slot shares, or
analysis-effective shares as survey incidence, audience prevalence beyond the
approved frame, measured performance, or confidence.

## Output Rules

- Use plain terms: segments, buyer mindsets, buyer situations, and panel profiles.
- State the directional scope once at the study or report level.
- Show source influence and evidence gaps without repeating the same warning.
- Integrate around research questions; do not report evidence lanes as disconnected summaries.
- Preserve important contradictions and negative cases in the marketer brief and technical appendix.
- Preserve exact source URLs and access dates.
- Keep social quotes short, traceable, and free of personal identifiers.
- Report the number of explicit panel profiles separately from later response jobs or model calls.
- For a paired run review, report requested/planned unique synthetic panelists (job slots), response jobs, accepted response records, retries, rejected provider returns, and model calls as distinct counts. Do not substitute the maximum synthetic-panelist ceiling for the planned total.
- Refuse unsupported weighting or prevalence claims.

## Stop Conditions

Stop before panel construction when the research brief is unapproved, required evidence lanes are empty without an accepted gap, source use is prohibited or unknown, sensitive traits would become operational attributes, or the proposed profile requires an unsupported joint combination.

Downgrade to Tier 1 when the required critical joint is missing, calculated
modeled share exceeds `0.30`, the frame is experimental or not defensible, or
a used overlay is experimental. Preserve the exact reason codes and lost
claims. An authorized calibration factor may be exactly `3.0`; anything above
`3.0` stops at validation.

Stop before package proposal when the workflow is not `approved`, the blind construction audit does not pass, or the brief, panel, evidence synthesis, report-input, audit, workflow bindings, `evidence_synthesis` approval, and `panel_construction` approval do not agree exactly.

Stop before canonical build or registration when any proposal gate has become
stale or `package_registration` is missing, rejected, pending, or bound to any
hash other than the exact proposed package hash. Every failure occurs before
canonical output or library mutation. Dogfood stops after its report and
audit. Provisional work stays run-local and cannot use the canonical build or
registration commands.

For v3 construction, finish the validated brief, panel, population-frame
result, composition plan, and final validity profile (plus an optional
read-only outcome-feedback binding or non-executable calibration proposal).
Do not invoke the v2 package, registration, or library commands for those v3
documents. If synthetic reactions are requested, continue through the
co-shipped B2 run-planning and testing workflow using the validated v3 panel.

For the Experimental Persona Behavior Calibration Sandbox, stop without a
proposal or candidate on late, immature, observational-only, duplicated,
dependent, tampered, incompatible, confounded, contradictory, or
non-identifiable evidence; more than one hypothesis; a missing target persona;
any structural audience change; any hidden-truth exposure; a stale binding or
forbidden diff; an existing or aliased output; or any production package,
registration, promotion, activation, or mutation request.
