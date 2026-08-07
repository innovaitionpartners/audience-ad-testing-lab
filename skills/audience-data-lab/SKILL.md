---
name: audience-data-lab
version: 1.0.0
description: Profile authorized CSV, JSON, or XLSX audience source bundles for safe structural shape and privacy risk, or prepare privacy-reviewed aggregate evidence from private CSV, JSONL, or NDJSON CRM, customer, sales, product-usage, campaign, conversion, pipeline, revenue, or retention data. Use when the user needs safe source profiling, asks to anonymize CRM data, discover stable cohorts, model performance, validate a panel against outcomes, or create an approved first-party or performance evidence handoff. Do not use for public research, persona writing, panelist interviews, creative scoring, or dashboards.
---

# Audience Data Lab

Process private rows inside a controlled local data boundary. Release only validated aggregate evidence packages to Audience Panel Builder or Ad Testing Lab.

## Non-Negotiable Boundary

- Never place raw rows, direct identifiers, reversible identifiers, private messages, or person-level free text in an LLM prompt.
- Never copy private input data into the repository, reusable panel package, dashboard, or model context.
- Never treat identifier removal alone as anonymization.
- Never turn a synthetic tabular row, model cluster, propensity score, or outcome label directly into a synthetic panelist.
- Never let the same records both fit and validate a performance model.
- Never claim prospective validity from retrospective data.
- Do not invoke another skill. Produce a versioned handoff for the user or outer orchestrator.

## Routing

Read only what the route needs:

| Need | Read or run |
|---|---|
| Input, audit, and handoff schemas | `references/contracts.md` |
| Privacy boundary and release rules | `references/privacy-threat-model.md` |
| Clustering, prediction, and synthetic-data policy | `references/modeling-policy.md` |
| Three-response workflow, templates, validation loop, and example | `references/workflow-responses-and-example.md` |
| Install the local modeling dependency when absent | `python3 -m pip install -r requirements-private-data.txt` |
| Profile authorized CSV, JSON, or XLSX source shape and privacy risk | `python3 scripts/profile-authorized-audience.py <source...> --profile-id <id> --profile-version <version> --profiled-at <ISO-8601> --output <new-profile.json>` |
| Transform an approved aggregate mapping into a canonical handoff | `python3 scripts/transform-authorized-audience.py --profile <approved-source-profile.json> --mapping <approved-mapping.json> --input-root <source-bundle> --output-dir <new-directory> --transformer-version <version>` |
| Audit and prepare aggregate evidence | `python3 scripts/prepare-private-evidence.py <input.csv|jsonl> <intake.json> <output-dir>` |
| Validate an existing handoff | `python3 scripts/validate-private-evidence.py <handoff.json>` |
| Apply a user approval record | `python3 scripts/approve-private-evidence.py <draft.json> <approval.json> <approved.json>` |

## Three-Response Workflow

Use the exact staged templates in `references/workflow-responses-and-example.md`.

- **Response 1:** confirm permission, retention, every column classification, intended processing, non-exposure guarantees, expected outputs, and no-clobber behavior before processing rows. This intake decision is the entire response.
- **Response 2:** after the user approves Response 1, run the local preparation and validation loop, then present the readable methodology report and draft handoff for approval.
- **Response 3:** after the user approves the exact draft, apply only the approval record, revalidate, and deliver the approved handoff.

Do not load or process private rows during Response 1. Reading the header after permission is confirmed is allowed only to reconcile the user-approved classification before Response 2.

## Authorized-Source Profiling

Before private-data intake, an authorized source bundle may be inspected with
`profile-authorized-audience.py`. This route creates an
`authorized-audience-source-profile-v1` that records only file fingerprints,
structural shape, field names, scalar/value classes, null rates, candidate
relationships, unresolved safety issues, and privacy risks. It never copies
person values, rows, raw paths, or source samples into the profile.

The profile does not map fields, join files, transform data, infer ambiguous
meaning, de-identify records, or authorize downstream use. Candidate roles,
units, and denominators require confirmation. A detected name, handle, email,
phone, address, device/cookie/advertising ID, IP address, account ID, or
person-level event/transaction pattern routes to private aggregation; do not
try to remove identifiers in this route. Formula cells, external links,
macros, encryption, legacy workbooks, malformed files, and unsafe formats are
rejected.

Only `ready_for_mapping` or `needs_clarification` returns exit code 0.
`requires_private_aggregation` deliberately returns 4 after writing its new
profile. A rejected profile deliberately returns 5. Existing output paths are
never overwritten.

The complete direct-aggregate workflow is:

```text
profile → mapping approval → deterministic transform → hash-bound handoff
```

After profiling, stop for review. Resolve every unit, denominator, join key,
category rule, missing/suppression rule, ignored field, expected output, and
semantic route in an `authorized-audience-mapping-v1`. Every profiled table and
field must be selected or explicitly ignored by its file, sheet, and record
path. Every selected and derived field needs one semantic route, including
fields later dropped as unsupported. For `needs_clarification`, list every
approved clarification resolution explicitly; mappings cannot override privacy
risk or unresolved profile issues. Do not read source rows while proposing or
approving the mapping. The exact profile hash and every input hash must be
bound into the mapping, and the data owner must approve its null-digest
canonical hash before transformation.

Each expected output must also declare the complete route-specific `metadata`
contract described in `references/contracts.md`. Bind canonical identifiers,
provenance, permissions, source fields, frame uncertainty, social observation
details, and outcome semantics explicitly. Never infer these from a field name
or source value. Frame cell metadata, structured item metadata, and social
observation metadata must exactly cover their records. Emit one
outcome-feedback file for each unique exact `record_match`; the match must
resolve one aggregate row.

Run `transform-authorized-audience.py` only after approval. It validates the
complete mapping before opening an input, reads each selected file from one
immutable byte snapshot, and verifies that the source did not change during the
read. It applies only the named declarative operation allowlist, enforces the
canonical route/schema registry and minimum cell size, and constructs the
actual downstream frame, structured evidence, social observation, and outcome
documents. It runs the authoritative downstream validators before
serialization, hashing, reporting, writing, and handoff publication. Handoff
validation repeats those semantic checks against the published canonical
bytes. It writes exact approved profile and mapping copies, records every loss
and coverage consequence, and emits an `authorized-audience-handoff-v1`.
Publication is transactional:
validation completes in a sibling staging directory before one atomic
no-replace rename, and any failure removes the staging directory. A destination
created during publication is never replaced. Existing output directories fail
without modification.

Counts, shares, weights, denominators, and structural estimates may enter only
the `structural_frame` route. Qualitative evidence may enter
`overlay_evidence` or `profile_seed`, but neither route can become structural
weight. A `profile_seed` remains evidence requiring Panel Builder construction
review; it is not a reusable profile.

If profiling returns `requires_private_aggregation`, do not create a direct
mapping, drop identifiers, or transform in place. Use the permissioned
private-data intake, aggregate evidence preparation, validation, and approval
workflow below. Only its approved anonymized aggregate handoff may return to a
new authorized-source profile and mapping cycle.

Create a separate intake, approval sequence, audit, and handoff for inputs with
different data kinds, entity or observation units, purposes, time windows, or
column schemas. For example, CRM contacts and campaign-performance rows are two
tracks even when the user supplies them together. This skill does not join raw
files. If the decision requires a join, stop and require the data owner to
provide a separately permissioned, purpose-built input with one declared unit.

## Processing Method

### 1. Freeze Purpose And Permission

Create `audience-private-data-intake-v1`. Name the data owner, permitted purpose, covered population, time window, entity unit, outcome where applicable, allowed columns, prohibited uses, minimum cell size, release mode, and retention rule.

Stop if permission is unconfirmed, the purpose is vague, the person or account unit is unknown, or raw-data retention is undefined.

### 2. Classify Every Column

Classify each input column exactly once as:

- entity identifier;
- direct identifier;
- quasi-identifier;
- sensitive;
- analysis dimension;
- numeric metric;
- outcome;
- event date;
- ignored.

Unknown or multiply classified columns fail. Direct identifiers, sensitive fields, ignored fields, and raw free text never enter modeling features or released aggregates.

### 3. Audit Before Modeling

Run `prepare-private-evidence.py`. It must complete:

- schema and input-fingerprint validation;
- missingness and usable-row analysis;
- direct-identifier and quasi-identifier review;
- small-cell and rare-combination risk;
- contribution and duplicate-entity review;
- time-window and outcome completeness;
- leakage checks;
- retention and release-mode checks.

Do not start machine learning when the audit is blocked.

### 4. Build Aggregate Evidence

For CRM or customer evidence, produce `audience-first-party-evidence-v1` with privacy-safe distributions, cross-tabs, missingness, coverage, limitations, and optional exploratory segment candidates.

For performance evidence, produce `audience-performance-evidence-v1` with privacy-safe cohort outcomes, a frozen temporal split, optional retrospective model results, and a narrow calibration scope.

Suppress cells smaller than the approved minimum. Keep suppressed values out of the handoff rather than masking only their labels.

### 5. Add Modeling Only When Justified

Use descriptive evidence first.

For exploratory segmentation:

- use only approved analysis features;
- group rare categorical values before fitting;
- evaluate multiple cluster counts and seeds;
- require minimum cluster size, separation, and co-assignment stability;
- return candidates for research interpretation, never approved segments.

For performance modeling:

- define the outcome and decision before fitting;
- split chronologically when time is material;
- fit only on the earlier partition;
- report holdout metrics and baseline comparison;
- label the result `retrospectively_evaluated`;
- preserve an untouched prospective path for stronger validation.

### 6. Review Privacy And Utility

The default release is aggregate-only or small-cell-suppressed. Use k-anonymity or differential privacy only when the intended release warrants it and its parameters are approved.

SDV or another tabular synthesizer is optional for development or controlled sharing. Evaluate privacy and utility separately. Synthetic generation does not establish anonymity or audience validity.

### 7. Validate And Approve The Handoff

Deliver:

1. `data-methodology-report.html`;
2. `private-data-audit.json`;
3. either `audience-first-party-evidence.json` or `audience-performance-evidence.json`.

The human report is the approval surface. The JSON handoff remains `draft` until the user records approval identity and time. Apply only that approval with `approve-private-evidence.py`; it rejects every other field change. Downstream skills accept only an approved handoff whose hash matches the reviewed output.

Run the exact validate → fix → revalidate commands in `references/workflow-responses-and-example.md`. Do not request approval until the handoff validator passes and the report, audit, and handoff hashes reconcile.

## Output Rules

- Describe the covered population and data exclusions in plain language.
- Separate observed aggregates, model estimates, and exploratory candidates.
- Show row counts only when permitted and never expose suppressed-cell values.
- Explain what the model was asked to predict, the holdout period, and the baseline.
- State one scoped limitation note; do not repeat generic disclaimers.
- Keep raw input paths, secrets, tokens, and person-level values out of released files.

## Stop Conditions

Stop when permission is missing, a column is unclassified, a prohibited field enters analysis, the minimum cell size is invalid, a requested privacy method is unavailable, a cluster is too small, a performance split leaks future outcomes, or released output contains a direct or reversible identifier.
