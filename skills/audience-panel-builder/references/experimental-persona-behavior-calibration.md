# Experimental Persona Behavior Calibration Sandbox

This sandbox uses fictional synthetic fixtures to propose and materialize a draft update to one existing persona. It does not validate real-world accuracy, cannot create a reusable package, and cannot register or activate a panel.

Every sandbox report must also display:

> Built and evaluated with fictional synthetic fixtures only. This output does not validate real-world panel accuracy, does not prove that the proposed change will improve outcomes, and cannot modify an active panel.

The sandbox verifies contracts, platform normalization, evidence-history
replay, bounded proposal logic, candidate construction, unchanged Ad Testing
machinery, hidden-answer separation, and production non-mutation. It does not
establish real customer behavior, predict performance, prove a preference, or
authorize a production change.

## Operating Sequence

Run commands from `skills/audience-panel-builder/`. Every output path must be
new. The commands reject existing targets, path aliases, symlinks, malformed
inputs, and unknown fields.

### 1. Freeze and generate the fictional study

Freeze the study manifest before generating any outcome. Keep public
engine-visible files and hidden truth in separate, new roots:

```bash
python3 scripts/build-synthetic-persona-behavior-study.py \
  --manifest-output <new-study-manifest.json> \
  --public-fixtures-root <new-public-root> \
  --oracle-fixtures-root <new-oracle-root> \
  --created-at <timestamp>
```

GitHub may contain only the generated fictional aggregate fixtures. Never use
client data, person-level data, production authority keys, or protected real
outcomes in this study.

### 2. Register creative attributes before outcome access

The registration input is a closed JSON document. It must bind creative asset
hashes, annotation methods, the reviewed behavioral hypothesis, and timestamps
proving registration preceded outcome access:

```bash
python3 scripts/register-synthetic-creative-attributes.py \
  --input <attribute-registration-input.json> \
  --output <new-attribute-registry.json>
```

A late attribute is invalid evidence. The engine may select only a frozen
hypothesis; it may not invent a post-outcome persona explanation.

### 3. Normalize each platform export without pooling semantics

Normalize Meta, Google, LinkedIn, and TikTok separately:

```bash
python3 scripts/import-synthetic-platform-outcomes.py \
  --platform <meta|google|linkedin|tiktok> \
  --input <raw-fictional-export.json> \
  --source-sha256 <sha256-digest> \
  --study-manifest <study-manifest.json> \
  --attribute-registry <attribute-registry.json> \
  --output <new-canonical-observations.json>
```

Clicks, denominators, attribution windows, reporting basis, maturity,
fractional values, modeled values, missing values, suppression, omitted zeros,
and observed zeros remain distinct. Incompatible platform, metric,
denominator, attribution, currency, timezone, or maturity contexts are not
pooled.

### 4. Build and replay the synthetic Outcome Evidence Library

Initialize one new synthetic-only library:

```bash
python3 scripts/manage-synthetic-outcome-evidence-library.py init \
  --library-root <new-library-root> \
  --library-id <library-id> \
  --created-at <timestamp>
```

Append one canonical observation and its frozen registry:

```bash
python3 scripts/manage-synthetic-outcome-evidence-library.py append \
  --library-root <library-root> \
  --observation <observation.json> \
  --attribute-registry <attribute-registry.json> \
  --ingested-at <timestamp>
```

Record a correction as a new immutable event. Never overwrite the superseded
entry:

```bash
python3 scripts/manage-synthetic-outcome-evidence-library.py correct \
  --library-root <library-root> \
  --superseded-entry-id <entry-id> \
  --replacement-observation <replacement-observation.json> \
  --attribute-registry <attribute-registry.json> \
  --reason <correction-reason> \
  --corrected-at <timestamp>
```

Inspect and authenticate an as-of projection:

```bash
python3 scripts/manage-synthetic-outcome-evidence-library.py list \
  --library-root <library-root> \
  --as-of <timestamp>

python3 scripts/manage-synthetic-outcome-evidence-library.py show \
  --library-root <library-root> \
  --entry-id <entry-id> \
  --as-of <timestamp>

python3 scripts/manage-synthetic-outcome-evidence-library.py verify \
  --library-root <library-root> \
  --as-of <timestamp> \
  --expected-head-receipt <head-receipt.json>
```

Treat longitudinal creative-feature findings as associations. Never convert
them into causal, preference, or persona-fact claims.

### 5. Diagnose and seal one bounded proposal

The diagnosis and proposal CLIs each accept one closed input document that
binds the study, open scenario manifests, experiment designs, evidence
projection and receipt, attribute registry, alternative-cause evidence, and
timestamps:

```bash
python3 scripts/diagnose-experimental-persona-behavior.py \
  --input <diagnosis-input.json> \
  --output <new-diagnosis.json>

python3 scripts/propose-experimental-persona-behavior-update.py \
  --input <proposal-input.json> \
  --output <new-proposal.json>
```

For an accepted update, the only operation is one
`profile_snapshot_update` against one existing persona and exactly one of
`anxieties`, `decision_context`, `motivations`, `proof_needs`, or
`role_context`. Audience membership, composition, weights, allocations,
capacity, prompts, responses, scoring, aggregation, and every unrelated
persona remain unchanged.

Diagnosis and proposal are registered private-stage roles. A qualifying run
must invoke the completed roles through the private staging provider so their
readable source, declared inputs, environment, and filesystem exclude oracle
code and hidden truth.

### 6. Materialize the sandbox candidate

Only a valid `profile_snapshot_update` proposal may create a candidate:

```bash
python3 scripts/materialize-experimental-persona-candidate.py \
  --base-panel <saved-audience-panel-v3.json> \
  --proposal <proposal.json> \
  --study-manifest <study-manifest.json> \
  --scenario-manifests <scenario-manifests.json> \
  --experiment-designs <experiment-designs.json> \
  --diagnosis <diagnosis.json> \
  --attribute-registry <attribute-registry.json> \
  --evidence-library-snapshot <library-snapshot.json> \
  --evidence-head-receipt <head-receipt.json> \
  --alternative-causes <alternative-causes.json> \
  --candidate-id <candidate-id> \
  --candidate-version <newer-semantic-version> \
  --created-at <timestamp> \
  --output-dir <new-candidate-directory>
```

The command authoritatively validates the base panel, derives its persona
projection, recomputes the proposal, applies the one allowed behavioral field,
validates the complete standalone candidate, and publishes an exact diff.
The result is a sandbox-only candidate directory, not a reusable panel
package. Standard registration and activation must reject it without changing
any production library byte or active pointer.

No-change, abstain, insufficient, and invalid results create no candidate.

### 7. Exercise base and candidate panels through the staged runtime

The registered private-stage `exercise` role maps to:

```bash
python3 scripts/run-synthetic-persona-behavior-exercise.py \
  --study-manifest <study-manifest.json> \
  --public-scenarios-root <public-scenarios-root> \
  --creative-attribute-registry <attribute-registry.json> \
  --base-panel <saved-audience-panel-v3.json> \
  --candidate-bindings-and-panels <sealed-candidate-envelope.json> \
  --exercise-id <exercise-id> \
  --exercised-at <timestamp> \
  --output <new-exercise.json>
```

Run this role only through the real private staging provider. It admits the
manifest-declared public files, the exact sealed candidate, and the pinned
NumPy/SciPy closure. It denies oracle-root reads and oracle-package imports.
The exercise calls the unchanged capacity, assignment, job/response,
screening, MaxDiff, pairwise, finalist, verbatim, complete-exposure, scoring,
and aggregation seams for every frozen scenario and repetition.

This is a deterministic machinery probe. It does not represent real panelist
judgment.

### 8. Reveal hidden truth only after engine outputs are sealed

The separate oracle-side evaluator joins the sealed engine results to hidden
truth:

```bash
python3 scripts/evaluate-synthetic-persona-behavior-proposal.py \
  --study-manifest <study-manifest.json> \
  --observations <observations.json> \
  --exercise <exercise.json> \
  --oracles <oracle-documents.json> \
  --diagnoses <diagnoses.json> \
  --proposals <proposals.json> \
  --candidates <candidates.json> \
  --phase-receipts <ordered-phase-receipts.json> \
  --evaluated-at <timestamp> \
  --output <new-evaluation.json>
```

The evaluator requires the ordered phase receipt chain, preserves every
scenario-family result, and reports failures, abstentions, uncertainty,
robustness, sensitivity, hidden-answer isolation, and zero production
mutation. It never emits one overall score.

### 9. Render the static report

```bash
python3 scripts/render-experimental-persona-behavior-report.py \
  --evaluation <evaluation.json> \
  --proposals <proposals.json> \
  --candidates <candidates.json> \
  --template assets/experimental-persona-behavior-report-template.html \
  --output <new-report.html>
```

The renderer accepts no oracle path. The report leads with the existing
persona behavior, proposed hypothesis, candidate state, exact diff,
supporting and contrary associations, alternative explanations, included
measurement contexts, failures, abstentions, and limits. Technical hashes
follow the plain-language sections.

## Repository And Private-Data Boundary

- Commit only deterministic fictional aggregate fixtures and their
  reproducibility manifests.
- Keep public scenario roots and hidden-oracle roots physically separate. The
  engine may receive only manifest-admitted public bytes.
- Never commit raw client data, person-level data, account identifiers,
  production outcomes, production authority keys, or protected real-evidence
  packages.
- Keep any later authorized aggregate real evidence in protected private
  storage under its separately approved contracts. It does not enter this
  sandbox or inherit authority from a synthetic result.
- Keep base panels, registered packages, production audience libraries,
  validation libraries, indexes, receipts, and active pointers read-only
  throughout every sandbox run and failed production-boundary attempt.

## Result States

| Visible state | Meaning | Candidate |
|---|---|---|
| **No change recommended** | Compatible synthetic evidence does not support the frozen bounded hypothesis. | None |
| **Unable to determine** | Evidence is insufficient, confounded, incompatible, contradictory, or non-identifiable. | None |
| **Behavioral update proposed** | One existing-persona, one-field synthetic hypothesis passed every frozen proposal gate. | Not yet created |
| **Sandbox candidate created** | The exact proposal was materialized into a separately versioned standalone panel for synthetic exercise only. | Sandbox only |
| **Evidence invalid** | A contract, hash, timing, authority, isolation, or history gate failed. | None |

## Stop Conditions

Stop without a proposal or candidate when any of these conditions occurs:

- fewer than two independent experiments or fewer than six complete eligible
  blocks per experiment;
- observational-only, immature, censored, missing, late-registered,
  duplicated, dependent, tampered, or incompatible evidence;
- an unresolved delivery, targeting, timing, offer, landing-page, tracking, or
  attribution explanation;
- contradictory evidence, non-identifiable twins, or more than one eligible
  behavioral hypothesis;
- a missing target persona, more than one target field, or any audience
  membership, composition, weight, allocation, profile-addition, or
  profile-retirement request;
- hidden truth or oracle code appearing in engine inputs, imports, readable
  paths, environment, source closure, or released engine output;
- a stale base panel, stale receipt, stale proposal, forbidden candidate diff,
  non-newer version, reused sealed holdout, or existing/aliased output path;
  or
- any request to package, register, promote, activate, or mutate a production
  panel from synthetic evidence.

On any isolation, contract, path, or production-boundary failure, preserve all
existing files and production state and stop.
