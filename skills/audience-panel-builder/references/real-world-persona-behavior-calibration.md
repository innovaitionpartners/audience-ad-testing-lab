# Experimental Real-World Panel Calibration

Use this Release C2 route only after Real-World Outcome Validation has produced
authenticated C1 evidence for an existing reusable v3 panel. This route is
experimental. It may register a new panel version only after the complete
sequence below passes. It never edits, replaces, or activates the original
panel.

Every C2 output and user-facing summary must say:

> Experimental only. Authenticated real-world outcome evidence supports a bounded persona-behavior hypothesis within the cited scopes; it does not prove causality, universal panel accuracy, or absolute performance.

## User-facing boundary

The user provides or identifies aggregate campaign-result exports for the saved panel, answers a targeted factual question only when a non-panel cause cannot be resolved from available evidence, later provides a fresh held-out result export, and explicitly approves or rejects the exact candidate and package registration. Route any new exports through Real-World Outcome Data Prep and C1 validation before invoking this Panel Builder route; Panel Builder does not process raw performance rows.

The skill owns authentication, repeated-miss discovery, evidence projection, alternative-cause review records, diagnosis, proposal selection, candidate materialization, exact diff and provenance, fresh validation preparation and evaluation, packaging, approval binding, and immutable new-version registration. Do not ask the user to build C1 packages, registries, JSON contracts, hashes, internal IDs, authority files, candidate directories, or registration proposals.

The pre-outcome creative-attribute registry is still a hard gate, but the workflow freezes it during study preparation. If it was not frozen before outcome access, stop; never ask the user to reconstruct or backdate it.

## Why this is a Panel Builder route

C2 changes one behavioral field in an existing panel. Panel Builder already
owns persona validation, versioned candidates, reports, package review,
approval scopes, immutable package registration, and library conflict rules.
A fifth skill would duplicate those authority boundaries. C2 is therefore a
post-validation Panel Builder route, separate from the six construction intake
routes and separate from the C1 validation route.

## Evidence roles

Keep three evidence sets disjoint:

1. **Diagnostic C1 evidence** identifies a repeated miss in the original panel.
2. **Candidate construction evidence** is the exact diagnosis, pre-outcome
   creative-attribute registry, alternative-cause review, proposal, panel
   candidate, and diff.
3. **Fresh held-out C1 evidence** evaluates the completed candidate. It cannot
   reuse a diagnostic study ID or source export.

The diagnostic evidence set must contain at least two authenticated C1
negative packages bound to the exact same base panel package. Study IDs and
outcome-source bytes must be disjoint across packages. Each package
must otherwise pass chronology, coverage, sample, independence, leakage,
multiplicity, repeated-look, and power gates. Limited, invalid, descriptive,
in-sample, or unauthenticated evidence cannot diagnose a behavioral miss.

## Skill-executed operating sequence

The skill executes the following operations from
`skills/audience-panel-builder/`. The commands document the governed runtime
sequence for maintainers; they are not customer instructions. Every output
path must be new. Keep authority secrets outside the candidate, reports, and
reusable package.

### 1. Freeze the behavior hypothesis before outcomes

Use the existing `creative-attribute-registry-v1` contract. It must bind exact
creative asset hashes and one or more reviewed Boolean attributes before the
earliest outcome access. A behavioral hypothesis names:

- one existing persona;
- exactly one of `anxieties`, `decision_context`, `motivations`,
  `proof_needs`, or `role_context`;
- one proposed after value;
- its rationale and abstention conditions.

Post-outcome annotations cannot support C2. The registry must contain both
informative and reference creatives for each eligible hypothesis.

Build the registry with the existing attribute-registry implementation through
the C2-named route:

```bash
python3 scripts/register-real-world-creative-attributes.py \
  --input <reviewed-pre-outcome-attribute-input.json> \
  --output <new-creative-attribute-registry.json>
```

The input uses the existing registry builder fields: `registry_id`,
`registered_at`, exact `creative_bindings`, reviewed `attribute_definitions`,
`creative_attributes`, `annotation_methods`, `reviewed_by`, `reviewed_at`, and
the declared `earliest_outcome_accessed_at` boundary.

### 2. Review alternative causes

Create `real-world-persona-alternative-causes-v1` with exactly these sorted
causes:

```text
attribution
delivery
landing-page
offer
targeting
timing
tracking
```

Each cause is `cleared` or `not_cleared` and includes review evidence. Any
uncleared cause stops proposal creation. The named review must occur after the
diagnostic outcomes were accessed and no later than diagnosis.

### 3. Diagnose and materialize one candidate

```bash
python3 scripts/materialize-real-world-persona-candidate.py \
  --base-panel-package <registered-base-panel-package.zip> \
  --diagnostic-validation-package <c1-negative-a.zip> \
  --diagnostic-validation-package <c1-negative-b.zip> \
  --attribute-registry <creative-attribute-registry.json> \
  --alternative-causes <alternative-causes.json> \
  --target-persona-id <existing-persona-id> \
  --target-segment-id <registered-segment-id> \
  --diagnosis-id <new-id> \
  --diagnosed-at <timestamp> \
  --proposal-id <new-id> \
  --proposed-at <timestamp> \
  --candidate-id <new-id> \
  --candidate-version <newer-semantic-version> \
  --created-at <timestamp> \
  --authority-registry <trusted-registry.json> \
  --authority-secret-file <owner-only.key> \
  --output-dir <new-candidate-directory>
```

The command authenticates each C1 package through the existing C1 validator,
projects those packages through the existing Outcome Evidence Library module,
and selects a hypothesis only when every independent package supports it with
no contrary registered contrast. More than one eligible hypothesis is
non-identifiable. The C2 bundle stores that exact authenticated projection as
`authenticated-outcome-evidence-library.json`; it does not translate real C1
evidence into fictional sandbox observations.

The materialized candidate:

- is a complete standalone v3 panel with a newer version;
- changes one persona behavioral field and the same field in every matching
  grounded profile snapshot;
- appends one experimental provenance row to `calibration_history`;
- retains the exact base and candidate authoring projections;
- retains every before/after value and changed JSON path;
- preserves audience membership, segments, structural weights, overlays,
  allocations, capacity, prompts, responses, scoring, aggregation, and every
  unrelated persona; and
- is not yet a reusable package and cannot be registered.

No-change, contrary, ambiguous, insufficient, invalid, or alternative-cause
results create no candidate.

### 4. Review and package through existing infrastructure

Render the candidate panel review and rerun the existing blind construction
audit. Use the existing v3 report, workflow-state, package, and package-review
commands. The candidate package must contain the exact
`candidate-audience-panel.json` bytes from the C2 directory. Do not hand-edit
the panel, strip the C2 calibration-history marker, or reuse the base package's
stale report, audit, workflow, or package approvals.

Standard audience-library registration rejects any package marked with
`experimental_c2_candidate_requires_gated_registration`. Building a package is
therefore not authority to register it.

### 5. Evaluate the packaged candidate with fresh held-out outcomes

Run the existing C1 workflow against the exact candidate package:

1. freeze a new candidate synthetic result;
2. preregister the C1 design before outcome access;
3. collect permissioned aggregate outcomes from new studies and source exports;
4. run the unchanged C1 evaluator; and
5. build the unchanged authenticated C1 validation package.

C2 registration requires `tier4_supported`,
`all_required_gates_passed: true`, and an active exact-scope C1 claim. The
candidate evaluation must occur after candidate materialization. Its study IDs
and source hashes must not overlap the diagnostic evidence.

### 6. Calculate the registration proposal

```bash
python3 scripts/propose-real-world-panel-registration.py \
  --candidate-bundle <candidate-directory> \
  --base-panel-package <registered-base-panel-package.zip> \
  --diagnostic-validation-package <c1-negative-a.zip> \
  --diagnostic-validation-package <c1-negative-b.zip> \
  --candidate-panel-package <candidate-panel-package-v3.zip> \
  --fresh-validation-package <candidate-c1-supported.zip> \
  --registered-at <planned-registration-timestamp> \
  --authority-registry <trusted-registry.json> \
  --authority-secret-file <owner-only.key> \
  --output <new-registration-proposal.json>
```

This command reauthenticates every C1 package, replays diagnosis, proposal,
candidate, and diff byte-for-byte, authenticates the candidate package, checks
fresh-evidence separation, and seals the exact registration proposal. It stops
with `awaiting_explicit_human_approval`.

### 7. Record explicit human approval

Use `panel-workflow-state-v1`. It must identify the exact candidate panel and
version and contain:

- an approved `calibration` scope targeting the exact unprefixed
  `registration_proposal_sha256`;
- an approved `package_registration` scope targeting the exact candidate
  package SHA-256;
- the exact candidate panel and package hashes in `bindings`; and
- named human reviewer metadata with approval timestamps after the fresh C1
  evaluation and no later than registration.

Changing any candidate, package, diagnosis, C1 package, evidence projection,
fresh evaluation, registration time, or proposal byte makes the approval stale.

### 8. Register through the gated C2 route

```bash
python3 scripts/register-real-world-calibrated-panel.py \
  --candidate-bundle <candidate-directory> \
  --base-panel-package <registered-base-panel-package.zip> \
  --diagnostic-validation-package <c1-negative-a.zip> \
  --diagnostic-validation-package <c1-negative-b.zip> \
  --candidate-panel-package <candidate-panel-package-v3.zip> \
  --fresh-validation-package <candidate-c1-supported.zip> \
  --registration-proposal <approved-registration-proposal.json> \
  --workflow-state <approved-c2-workflow-state.json> \
  --registered-at <same-planned-registration-timestamp> \
  --authority-registry <trusted-registry.json> \
  --authority-secret-file <owner-only.key> \
  --library-root <audience-library>
```

The command repeats every authentication and replay check, validates both
human approvals, and then calls the existing immutable audience-library
registration transaction. Registration creates one new version or returns
`already_registered` for the exact same bytes. A version collision with
different bytes fails. No active pointer is changed and no base-panel byte is
written.

## Result states

| State | Meaning | Registration |
|---|---|---|
| No repeatable miss | Authenticated C1 results do not support one repeated behavior hypothesis. | Prohibited |
| Unable to determine | Evidence is contrary, ambiguous, confounded, limited, or non-identifiable. | Prohibited |
| Experimental update proposed | One existing-persona, one-field hypothesis passed diagnosis. | Prohibited |
| Experimental candidate created | A newer complete panel and exact diff exist. | Prohibited pending fresh C1 |
| Fresh candidate validation failed | The candidate was negative, limited, invalid, stale, or overlapping. | Prohibited |
| Awaiting explicit human approval | All evidence gates passed and the exact registration proposal is sealed. | Prohibited |
| Experimental candidate registered | Exact approvals passed and the immutable new version was registered. | Permitted for that exact package only |

Registration does not prove that the profile edit caused the fresh result. It
establishes only that the experimental candidate passed the stated governed
workflow and may be used as a separately versioned panel within its documented
limits.
