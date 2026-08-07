# Operator Guide

Use this reference to conduct the conversation and run the shipped workflow.
Keep the user on plain-language review surfaces. The skill, not the user,
creates strict JSON inputs from authenticated files and focused answers.

## Contents

- [Start safely](#start-safely)
- [Prepare Study conversation](#prepare-study-conversation)
- [Import Results conversation](#import-results-conversation)
- [Strict per-source context](#strict-per-source-context)
- [Adapter maturity](#adapter-maturity)
- [Result language](#result-language)
- [CLI sequence](#cli-sequence)

## Start Safely

1. Confirm the selected mode.
2. Confirm the user-visible destination. If absent, propose a visible study
   folder and ask before writing. Never use a hidden runtime path.
3. Check that the destination does not exist; every draft, sealed study, and
   import generation is new and immutable.
4. Use the plugin manager's installed copy or one deliberate local checkout.
   If multiple clones exist, confirm the active runtime root before live
   preparation. Every installation must pass the same release and repository
   identity checks.
5. Keep authority secrets outside the visible study folder. Never copy secret
   values into a command transcript, report, or durable output.

## Prepare Study Conversation

First read the supplied Ad Testing Lab run, panel package, producer result,
creative roster and assets, testing map, frozen prediction, and campaign plan.
Derive all possible setup and delivery-map fields before asking anything.

Ask one unresolved question at a time, in this order when needed:

1. primary metric;
2. metric direction;
3. measurement window;
4. attribution window;
5. validation blocks;
6. minimum effect;
7. missing-data rule;
8. permission reference;
9. delivery-start evidence;
10. outcome-access attestation;
11. registration identity; and
12. approval identity.

Skip any answer already established by authenticated input. Do not expose the
contract as a questionnaire. Do not ask the user to copy prediction values,
creative IDs, hashes, platform IDs, or result rows that the supplied files can
provide.

After drafting, present `study-summary.md` and state:

- what will be measured and in which direction;
- which frozen prediction and creative identities are bound;
- which audience, blocks, dates, window, and comparison are registered;
- whether delivery identities are complete;
- whether chronology is `preregistered_holdout`, `descriptive_only`, or
  `blocked`; and
- any unresolved question.

Stop for explicit approval of that exact review. Seal only an approved,
complete draft. A study whose outcomes were already available or inspected is
descriptive and cannot be made held out by editing a date.

## Import Results Conversation

Require the frozen study folder and original aggregate exports downloaded from
the advertising platforms. Accept CSV, TSV, XLSX, JSON, or supported ZIP
packages only through the shipped safety inspection.

For each uploaded file, derive filename, bytes, size, exact source SHA-256,
container, sheets or tables, headers, row grain, schema fingerprint, detectable
adapter, observed minimum group size, and privacy result. Then ask only for
facts that cannot be derived:

- data owner and system of record;
- permission reference and confirmer;
- allowed purpose and retention policy;
- minimum group-size rule;
- confirmation that restricted fields were removed;
- export method and timestamp when the file cannot establish them;
- platform reporting timezone, currency basis, report time basis,
  attribution semantics or windows, selected metric, and latency state when
  the exact export does not establish them; and
- an explicit generic source-column mapping when no named exact adapter is
  valid.

Never ask for campaign outcome values. Never accept CRM, analytics, revenue,
retention, person, event, log, device, cookie, or user-level files. If a source
contains prohibited or too-small data, preserve only a checksum and redacted
rejection reason; do not retain prohibited bytes or values.

One source, one governance input, and one source context form an ordered set.
Pass repeated `--source`, `--source-governance`, and `--source-context`
arguments in the same order. Never reuse one source's context by filename or
because two files share a schema.

## Strict Per-Source Context

The strict per-source context is generated after upload by the skill. It binds
the exact source SHA-256 and protected inventory to the authenticated study and
sealed delivery map, then adds only the reporting and adapter facts that cannot
be derived safely. The user answers plain questions; the user never authors
the source-context JSON.

Generate one source context per uploaded file. Every named exact context has:

```json
{
  "adapter_registration": "<generated closed adapter registration>",
  "reporting_metadata": "<generated closed reporting metadata>",
  "source_binding": {
    "source_sha256": "sha256:<exact uploaded bytes>",
    "inventory_sha256": "sha256:<protected inventory>",
    "study_id": "<authenticated study ID>",
    "delivery_map_sha256": "sha256:<sealed delivery map>"
  }
}
```

The displayed strings for the two generated objects are explanatory
placeholders, not valid runtime input. Materialize their exact platform shape
from the selected adapter and authenticated study.

For generic programmatic CSV, generate a context with adapter ID
`generic-dsp-mapping-v1`, the same source binding, the sealed delivery-map
digest, complete reporting metadata, and an explicit mapping from each chosen
source column to one canonical field. The mapping must be one-to-one, contain
exactly one `conversion_value`, exactly one `currency`, and at least one stable
`creative_id` or `ad_id`. Present the proposed mapping for approval. Do not auto-detect a generic mapping.
Do not infer identity from labels or authorize a new metric, denominator,
attribution rule, or reporting basis after outcomes are visible.

If exact identity was not sealed before outcome access, quarantine the row or
keep the import descriptive. A user confirmation after outcome access cannot
restore held-out status.

## Adapter Maturity

Read `references/platform-capabilities.json` for the exact current variant,
container, locale, schema fingerprint, version, and maturity. A platform name
alone never proves support.

- `export_verified`: the exact variant has passed a sanitized genuine export
  check and may be `contract_ready` if every other rule passes.
- `schema_tested`: the parser has only the documented-schema evidence recorded
  in the registry. The import remains `incomplete`. Controlled fixtures cannot
  establish real-world operability or market behavior.
- `blocked`: the exact source shape is unavailable, unsafe, or incompatible.

Amazon DSP is blocked in this release pending an admitted sanitized sample for
its exact source shape. Do not route it through generic programmatic merely to
bypass that block. The generic adapter itself is `schema_tested`, requires the
explicit mapping above, and therefore remains incomplete.

## Result Language

Use the readiness report's exact state without adding an outcome judgment:

- **Contract-ready:** “The uploaded aggregate files are prepared and may be
  handed to Real-World Outcome Validation. This preparation does not say
  whether the panel was right.”
- **Incomplete:** “The study package is incomplete because: <runtime reasons>.
  An effectively preregistered, schema-tested import may include a structural
  `validation-handoff.json`, but it is not contract-ready or authorized for
  downstream Real-World Outcome Validation until every readiness requirement,
  including genuine export verification, is met.”
- **Descriptive only:** “This historical or after-the-fact import is preserved
  for description only. It cannot be upgraded to preregistered held-out
  evidence. Descriptive-only imports have no validation handoff.”
- **Blocked:** “Preparation stopped because: <runtime reasons>. The safe next
  action is: <specific remediation>.”

Historical imports always remain `descriptive_only`, even when their source
shape is otherwise supported. Never say the preparation compares creatives,
confirms ordering, judges evidence, validates a panel, or calibrates a persona.

## CLI Sequence

Run from the complete plugin root. Use each script's `--help` for all current
arguments.

Prepare Study uses two separate approval-gated calls:

```bash
python3 skills/real-world-outcome-data-prep/scripts/prepare-outcome-study.py \
  draft --setup-input <generated-setup.json> \
  --output-dir <new-visible-draft-folder>

python3 skills/real-world-outcome-data-prep/scripts/prepare-outcome-study.py \
  seal --draft-dir <approved-draft-folder> \
  --output-dir <new-visible-study-folder> \
  --authority-root <protected-authority-root> \
  --authority-index <protected-authority-index.json> \
  --authority-registry <trusted-authority-registry.json> \
  --authority-secret-file <owner-only-secret-file>
```

Import Results repeats all three source arguments in matching order:

```bash
python3 skills/real-world-outcome-data-prep/scripts/import-outcome-results.py \
  --study-root <visible-study-folder> \
  --source <uploaded-platform-file-1> \
  --source-governance <generated-governance-1.json> \
  --source-context <generated-context-1.json> \
  --source <uploaded-platform-file-2> \
  --source-governance <generated-governance-2.json> \
  --source-context <generated-context-2.json> \
  --authority-registry <trusted-authority-registry.json> \
  --authority-secret-file <owner-only-secret-file> \
  --imported-at <timezone-aware-timestamp> \
  --import-id <new-import-id>
```

Use the correction flags shown by `--help` only for a complete replacement
generation bound to the authenticated prior import and all superseded
observations.

Validate or recover without evaluating outcomes:

```bash
python3 skills/real-world-outcome-data-prep/scripts/validate-outcome-study.py \
  --study-root <visible-study-folder> \
  --authority-registry <trusted-authority-registry.json> \
  --authority-secret-file <owner-only-secret-file>

python3 skills/real-world-outcome-data-prep/scripts/recover-outcome-study.py \
  --study-root <visible-study-folder> \
  --authority-registry <trusted-authority-registry.json> \
  --authority-secret-file <owner-only-secret-file>
```

Validation here means structural and authority reauthentication of the
prepared study. Stop before Real-World Outcome Validation, comparison building,
eligibility, claims, evidence-library changes, persona work, or panel changes.
