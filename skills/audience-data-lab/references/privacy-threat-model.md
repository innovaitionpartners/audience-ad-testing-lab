# Private-Data Threat Model

## Contents

1. Protected material
2. Trust boundaries
3. Required controls
4. Release modes
5. Failure behavior

## Protected Material

Treat these as private even when the user does not label them:

- contact, account, opportunity, transaction, device, and employee identifiers;
- names, emails, phone numbers, addresses, handles, profile URLs, and free text;
- rare role, location, company, product, or behavior combinations;
- protected or sensitive traits;
- customer, pipeline, revenue, retention, and conversion outcomes;
- model outputs that preserve recoverable row-level information.

## Trust Boundaries

### Raw zone

The source file remains in the user-authorized location. Deterministic local code may read it. Do not copy it into the repo or send its rows to an LLM, connector, browser, analytics service, or synthetic-data API.

### Working zone

Keep only in-memory or explicitly approved local working data. Do not persist intermediate matrices by default. Never persist reversible mappings from tokens to source identifiers.

### Release zone

Release only the audit, readable methodology report, and approved aggregate handoff. Scan every release for direct identifiers, prohibited column names, rare values, raw input paths, and unexpected fields.

## Required Controls

- Confirm purpose, permission, data owner, entity unit, and retention before reading rows.
- Classify every column once.
- Exclude direct identifiers, sensitive fields, ignored fields, and free text from analysis.
- Treat quasi-identifiers as privacy-risk fields, not default model features.
- Suppress rare categories before aggregation or modeling.
- Bound one entity’s contribution when multiple rows per entity exist.
- Reject joins that create unreviewed high-cardinality combinations.
- Separate fitting and validation by time when outcomes are temporal.
- Hash inputs without copying their contents.
- Produce no raw-row preview.

## Release Modes

### Aggregate only

Default. Release distributions and cross-tabs only when cells meet the approved minimum.

### K-anonymous

Release only combinations whose contributing entity count meets `k`. This is a release-resolution control, not protection against every attribute-inference attack.

### Differential privacy

Require an approved privacy budget, contribution bounds, mechanism, engine version, accountant, and utility test. Do not imitate differential privacy by adding ad hoc noise.

### Synthetic tabular

Require a named synthesizer, fit configuration, disclosure-risk assessment, distance-to-closest-record or equivalent test, utility assessment, and approved release purpose. Do not call output anonymous merely because rows are generated.

## Failure Behavior

Fail closed when:

- permission is unconfirmed;
- columns are unclassified or multiply classified;
- direct identifiers appear in released output;
- small cells would be exposed;
- a requested formal privacy method is unavailable;
- an output cannot be reconciled to its input and configuration hashes;
- the retention plan is absent;
- private rows would enter a model prompt or external service.
