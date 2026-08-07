# Audience Data Lab

Audience Data Lab processes permissioned private audience or performance data locally and releases only privacy-reviewed aggregate evidence.

## Use it when

- CRM, customer, sales, product-usage, pipeline, revenue, retention, or campaign data should inform audience research.
- You need to profile an authorized source bundle for structural shape and privacy risk.
- Approved aggregate performance evidence is needed for a permitted calibration or validation use.

## Do not use it for

- Public audience research.
- Persona writing or synthetic panelist construction.
- Ad scoring, creative review, or dashboards.
- Sending raw rows into an AI prompt.

## Inputs

Permissioned local tabular data, a frozen purpose and population, field classifications, minimum cell size, release rules, and retention terms.

## Outputs

- Human-readable methodology report.
- Privacy and processing audit.
- Approved aggregate first-party audience evidence or performance evidence.

Raw rows, direct identifiers, private messages, and person-level free text never enter downstream panels or prompts.

## Start here

- [Use private audience data](../../docs/guides/use-private-audience-data.md)
- [Privacy and data boundaries](../../docs/reference/privacy-and-data-boundaries.md)
- [Outputs and files](../../docs/reference/outputs-and-files.md)
- [Technical skill instructions](SKILL.md)

## Next capability

Send only the approved aggregate handoff to [Audience Panel Builder](../audience-panel-builder/README.md) or the explicitly permitted downstream calibration workflow.
