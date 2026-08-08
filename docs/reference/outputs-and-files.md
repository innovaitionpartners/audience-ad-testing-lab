# Outputs and files

The workflow produces human review surfaces and machine-verifiable records. Marketers normally open the HTML, Markdown, CSV, and dashboard files first. JSON and ZIP files preserve contracts, lineage, and transfer integrity.

## Audience Data Lab

| File | Human use |
|---|---|
| `data-methodology-report.html` | Review what data was admitted, how it was processed, what was suppressed, and what the evidence can support |
| `private-data-audit.json` | Technical record of permission, classification, privacy, leakage, and release checks |
| `audience-first-party-evidence.json` | Approved aggregate audience evidence for Panel Builder |
| `audience-performance-evidence.json` | Approved aggregate performance evidence for a permitted calibration use |

Raw private inputs are never panel or dashboard deliverables.

## Audience Panel Builder

| File | Human use |
|---|---|
| `audience-panel-review.html` | Primary review of the complete audience, segments, profiles, unknowns, and evidence |
| `panel-summary.md` | Portable text summary of the same canonical panel |
| `audience-research-report.html` | Evidence synthesis, source support, conflicts, and limitations |
| sources CSV | Openable source inventory with direct links and audit metadata |
| `saved-audience-panel.json` | Canonical machine-readable panel; do not hand-edit the HTML or Markdown projections |
| `panel-review-manifest.json` | Binds the review to the exact panel and inputs |
| construction audit | Blind audit of profile construction and evidence support |
| immutable panel ZIP | Authorized transfer or registration package; not the primary preview |
| experimental calibration candidate bundle | Authenticated Outcome Evidence Library projection, diagnosis, one-field proposal, complete newer candidate, exact diff, and provenance; not registration authority |
| calibration registration proposal | Binds the candidate package to a fresh nonoverlapping Real-World Outcome Validation evaluation for exact human approval |

## Ad Testing Lab

| File | Human use |
|---|---|
| `dashboard.html` | Primary decision package and navigation surface |
| study manifest | Frozen method, audience, creative, capacity, output, and approval bindings |
| creative roster | Exact tested variations and media hashes |
| `panelist-responses.jsonl` | Accepted synthetic feedback records |
| `raw-provider-returns.jsonl` | Raw model-call lineage admitted by the run |
| `rejected-attempts.jsonl` | Failed validation attempts and errors |
| `dispatch-audit.jsonl` | Authorized jobs, call positions, attempts, and retry lineage |
| screening results | Deterministic first-round outcome and validity state |
| boundary results | Separate cutoff resolution for eligible partial-exposure runs |
| finalist results | Post-approval finalist feedback and conditional shares |
| feedback synthesis | Organized themes and dissent without recalculating the result |
| saliency index | Original/overlay attention evidence for imagery |

The dashboard Downloads tab exposes the records bound to the current run. Record counts and hashes must reconcile.

### Attempt lineage delivery

The run manifest binds the canonical paths and SHA-256 hashes for `raw-provider-returns.jsonl`, `rejected-attempts.jsonl`, accepted responses, and dispatch records. The dashboard downloads expose those bound records without changing their source bytes.

### Dashboard navigation

The marketer-facing dashboard preserves this navigation contract: Overview, Ads tested, Test audience, All ad results, Top ads, Feedback, Attention heatmap (imagery only), AI audience responses, Methodology, Downloads. The interface may label the technical explanation as Methodology/Test details, but it cannot omit the underlying method and run evidence.

## Real-World Outcome Data Prep

| File | Human use |
|---|---|
| `study-summary.md` | Review the preregistered metric, prediction, creatives, audience, dates, comparison, and unresolved questions |
| sealed study folder | Immutable pre-outcome registration and authenticated source bindings |
| `readiness-report.md` | Shows whether imported results are contract-ready, incomplete, descriptive-only, or blocked |
| `matching-report.md` | Shows how uploaded aggregate rows bind to the registered creatives and study |
| admitted source snapshots | Exact permissioned aggregate exports with provenance and hashes |
| immutable import generation | Normalized observations, quarantines, readiness, corrections, and lineage |

## Real-World Outcome Validation

| File | Human use |
|---|---|
| validation registration | Frozen design and authority record created before eligible outcomes |
| comparison records | Exact synthetic-versus-real ordering comparisons |
| evaluation report | Eligibility, uncertainty, coverage, missingness, and gate results |
| optional claim | Narrow expiring claim issued only after an authenticated passing evaluation |
| immutable validation package | Complete audit and evidence bundle |

## Which file should I open first?

- Reviewing an audience: open `audience-panel-review.html`.
- Reviewing research: open `audience-research-report.html` and the sources CSV.
- Reviewing an ad test: open `dashboard.html`.
- Reviewing private-data preparation: open `data-methodology-report.html`.
- Reviewing a real study before launch: open `study-summary.md`.
- Reviewing imported outcomes: open `readiness-report.md` and `matching-report.md`.
- Reviewing an experimental panel update: open the calibration diagnosis, exact diff, fresh validation report, and registration proposal together.

## Related guides

- [Build an audience panel](../guides/build-an-audience-panel.md)
- [Test ads](../guides/test-ads.md)
- [Use private audience data](../guides/use-private-audience-data.md)
- [Validate with real results](../guides/validate-with-real-results.md)
