# Use private audience data safely

## Use this when

Permissioned CRM, customer, sales, product-usage, pipeline, revenue, retention, conversion, or campaign data should inform audience research or later performance calibration. Use [Audience Data Lab](../../skills/audience-data-lab/README.md).

Do not send row-level private data directly to Audience Panel Builder or Ad Testing Lab.

## What you need

- Confirmed data owner and permitted purpose.
- Covered population, time window, and entity or observation unit.
- Approved columns and prohibited uses.
- Minimum cell size and release mode.
- Defined retention and deletion rules.

Processing stops when permission, purpose, unit, column classification, or retention is unclear.

## What happens locally

### 1. Classify the source

Every field is classified as an identifier, quasi-identifier, sensitive field, analysis dimension, metric, outcome, event date, or ignored field. Removing names alone is not treated as anonymization.

### 2. Audit before modeling

The workflow checks missingness, direct identifiers, rare combinations, small cells, duplicate contribution, time windows, outcome completeness, leakage, and release rules before any modeling begins.

### 3. Build aggregate evidence

Audience evidence may contain privacy-safe distributions, cross-tabs, coverage, missingness, limitations, and carefully labeled exploratory segment candidates.

Performance evidence may contain privacy-safe cohort outcomes, a chronological split, retrospective model results, and a narrow calibration scope.

Small cells are suppressed and omitted from the released handoff.

### 4. Review and approve

The human-readable methodology report is the approval surface. The machine-readable handoff remains a draft until the exact reviewed output is approved and revalidated.

## What never leaves the boundary

- Raw rows and person-level free text.
- Names, emails, handles, phones, addresses, account IDs, device IDs, or reversible identifiers.
- Unsuppressed small cells.
- Private input paths, secrets, or tokens.
- Synthetic rows or clusters presented as panelists.

Read [Privacy and data boundaries](../reference/privacy-and-data-boundaries.md).

## What you receive

- `data-methodology-report.html`
- `private-data-audit.json`
- approved audience or performance aggregate evidence JSON

Read [Outputs and files](../reference/outputs-and-files.md) for downstream use.

## Next step

- Send approved audience evidence to [Audience Panel Builder](build-an-audience-panel.md).
- Send approved performance evidence to the relevant calibration or validation workflow only when its allowed use permits it.
