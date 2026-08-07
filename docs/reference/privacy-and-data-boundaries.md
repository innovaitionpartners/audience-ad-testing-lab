# Privacy and data boundaries

The four capabilities have separate data permissions. Keeping those boundaries intact prevents private rows, person-level details, and real campaign outcomes from leaking into audience construction or synthetic testing.

## Accepted data by capability

| Capability | May read | Must not read or retain |
|---|---|---|
| Audience Data Lab | Permissioned local CSV, JSON, JSONL, NDJSON, or XLSX within an approved purpose | Unapproved sources or fields; prohibited uses |
| Audience Panel Builder | Public research, authorized supplied research, and approved aggregate handoffs | Raw CRM, customer, account, performance, or person-level rows |
| Ad Testing Lab | Exact creatives, approved panel packages, run-local provisional audiences, approved aggregate performance handoffs | Raw private rows or unapproved campaign/customer data |
| Real-World Outcome Data Prep | Uploaded aggregate advertising-platform exports and frozen study files | CRM, analytics, revenue, retention, person-, event-, log-, device-, or user-level data |

## Private-data rules

- Confirm the data owner, purpose, population, unit, time window, allowed fields, prohibited uses, retention, and release mode before processing.
- Classify every column exactly once.
- Direct identifiers, sensitive fields, ignored fields, and raw free text never become released features.
- Small cells are suppressed and omitted, not merely relabeled.
- Raw rows never enter prompts, repositories, reusable panels, dashboards, examples, or shared packages.
- Synthetic tabular rows, clusters, and propensity scores never become synthetic panelists.
- The same records cannot both fit and validate a performance model.

## Authorized source profiling

Structural profiling may record file fingerprints, shapes, field names, null rates, value classes, candidate relationships, and privacy risks without copying rows or values. It does not authorize transformation or downstream use.

A source containing person-level identifiers or event patterns routes to private aggregation. Removing identifiers in place is not a safe substitute.

## Aggregate handoffs

Downstream skills accept only validated and approved aggregate handoffs whose hash matches the reviewed output. Audience and performance evidence remain separate and carry explicit allowed uses.

## Real campaign outcomes

Outcome Data Prep accepts original aggregate advertising-platform exports only. It preserves admitted source bytes and provenance. Historical or after-the-fact evidence remains `descriptive_only`; chronology is never upgraded.

## Repository and examples

Public documentation and fixtures must not contain:

- person-level or account-level private data;
- proprietary calibration datasets;
- secrets, tokens, or private source paths;
- client-specific examples without explicit authorization;
- claims derived from evidence that is not shipped and inspectable.

## Related guides

- [Use private audience data](../guides/use-private-audience-data.md)
- [Build an audience panel](../guides/build-an-audience-panel.md)
- [Validate with real results](../guides/validate-with-real-results.md)
- Technical operators: [`Audience Data Lab SKILL.md`](../../skills/audience-data-lab/SKILL.md)
