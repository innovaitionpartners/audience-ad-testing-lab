---
name: real-world-outcome-data-prep
version: 1.0.0
description: Prepare a real advertising outcome study before launch or import uploaded aggregate Meta, Google Ads, LinkedIn, TikTok, DV360, The Trade Desk, Amazon DSP, Xandr, or generic programmatic result files after the campaign. Use when the user wants to preregister an Ad Testing Lab prediction, avoid manually filling the real-outcome template, normalize platform exports, preserve source provenance, or create a handoff for Real-World Outcome Validation. Do not use for CRM, analytics, person-level data, evaluating whether the panel was right, changing personas, or activating panel updates.
---

# Real-World Outcome Data Prep

Prepare uploaded aggregate advertising-platform files only. Preserve source
bytes, provenance, permission, chronology, platform reporting meaning, and
correction history. Do not ask the user to fill a durable contract or rekey
campaign results.

## Non-Negotiable Boundary

- Accept uploaded aggregate social, search, and programmatic ad-platform files
  only. Reject CRM, analytics, revenue, retention, person-level, event-level,
  log-level, device-level, and user-level data.
- This preparation step does not decide whether the panel was right.
- It does not compare or order results.
- It does not judge evidence.
- It does not calibrate personas.
- It does not materialize candidates.
- It does not activate changes.
- It does not mutate panels or libraries.
- Preserve one study's authenticated registration and immutable imports.
  Never overwrite a sealed registration, accepted source, generation, ledger,
  or prior correction.
- Treat `schema_tested` as `incomplete`; controlled fixtures cannot establish
  real-world operability. Treat an after-the-fact or historical study as
  permanently `descriptive_only`. Treat Amazon DSP as blocked until its exact
  source shape is available.
- Stop before Real-World Outcome Validation. Offer the unchanged downstream
  validation capability only as an explicit next step.

## Choose One Mode

Use **Prepare Study** before launch, before delivery, and before anyone has
accessed campaign outcomes. Use **Import Results** after the campaign when the
user has the frozen study folder and unedited aggregate platform exports.

If the user starts with results but no pre-outcome registration, use Import
Results and preserve `descriptive_only`; never manufacture an earlier
timestamp or upgrade chronology.

## Output-Folder Rule

Require a visible study folder chosen or approved by the user. If no path is
given, propose a visible path in the current project or Downloads; ask before writing.
Never write by default inside this skill, its runtime checkout,
another hidden directory, or a temporary directory presented as the result.
Every durable output path must be new.

## Prepare Study

1. Locate the exact Ad Testing Lab run, panel package, frozen producer result,
   creative roster and assets, testing map, prediction, and campaign plans.
2. Verify their existing hashes and contracts. Derive every safe field from
   those files, including delivery identities already present.
3. Ask one focused question at a time only for facts that cannot be derived,
   such as the primary metric, dates, attribution window, planned blocks,
   approval identity, or delivery IDs that now exist but precede outcome
   access. Never ask for facts already available in the supplied files.
4. Create the setup input described in
   [contracts.md](references/contracts.md), then run the draft command into a
   new visible draft folder.
5. Present `study-summary.md` as the review surface. Show the metric,
   prediction, creatives, audience, dates, comparison, evidence status, and
   every unresolved question.
6. Stop for the user's explicit approval. Do not seal while questions remain
   or when the user has not approved the exact review.
7. After approval, run the seal command into a new final study folder. If
   outcomes were already inspected, preserve `descriptive_only`.

## Import Results

1. Require the authenticated frozen study folder and one or more uploaded,
   unedited aggregate ad-platform exports.
   Generate a new import identity and import timestamp.
2. Read [operator-guide.md](references/operator-guide.md). Gather only the
   permission, retention, export, adapter, and reporting facts that cannot be
   derived from each uploaded source.
3. Snapshot, hash, inventory, and privacy-scan every source before durable
   admission. Generate one strict source-governance input and one strict
   per-source context after upload; bind the context to that exact source
   SHA-256, protected inventory, study, and sealed delivery map.
4. Use exact content and schema detection for a named adapter. For generic
   programmatic data, require an explicit one-to-one source-column mapping;
   never infer or auto-approve it. Keep source, governance, and context
   arguments in the same repeated order.
5. Run the import command once for the complete set. It preserves admitted
   bytes, rejects prohibited sources without retaining their values,
   quarantines incompatible rows, normalizes recognized observations, creates
   structural bindings, derives readiness, and publishes one immutable
   generation.
6. Read `readiness-report.md` and `matching-report.md` to the user. Do not
   interpret campaign performance.
7. For a correction, require the prior import and observation identities plus
   all correction facts. Publish a new generation; never edit the earlier one.

## Handle The Result

- `contract_ready`: explain that the prepared package may be handed to
  Real-World Outcome Validation. Do not run validation without the user's
  explicit next-step request.
- `incomplete`: name the exact missing, quarantined, or not-yet-verified
  source condition. `schema_tested` remains incomplete.
- `descriptive_only`: explain that chronology permits description only and can
  never be upgraded to preregistered held-out evidence.
- `blocked`: identify the failed permission, privacy, authority, chronology,
  source-integrity, or adapter rule and the safe next action.

For any state, stop before evaluation, comparison construction, outcome
ordering, uncertainty, eligibility, sufficiency, claims, evidence-library
writes, persona diagnosis, calibration, candidate work, or panel changes.

## Routing

Read or run only what the selected phase needs:

| Need | Read or run |
|---|---|
| Install spreadsheet support when absent | `python3 -m pip install -r requirements-outcome-data-prep.txt` |
| Closed document shapes and status rules | `references/contracts.md` |
| Operator questions, per-source context, adapter maturity, and examples | `references/operator-guide.md` |
| Draft or seal a study | `python3 scripts/prepare-outcome-study.py <draft|seal> ...` |
| Import results or publish a correction | `python3 scripts/import-outcome-results.py ...` |
| Reauthenticate and inspect a prepared study | `python3 scripts/validate-outcome-study.py ...` |
| Recover one interrupted immutable publication | `python3 scripts/recover-outcome-study.py ...` |

Use `--help` for the exact current CLI arguments. These scripts are the
authorities; do not recreate their validation or publication behavior in
prose or ad hoc code.
