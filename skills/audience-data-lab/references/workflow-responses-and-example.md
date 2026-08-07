# Workflow Responses And Worked Example

## Contents

1. Response 1: intake approval
2. Response 2: methodology and draft approval
3. Response 3: approved handoff
4. Validation loop
5. Worked example

## Response 1: Intake Approval

Produce only this response before processing private rows. Use “ready for
approval” only when the decision is ready; otherwise use “blocked” in the
heading.

```markdown
## Private-data intake [ready for approval | blocked]

- Track: [one data kind, purpose, and unit; e.g. CRM audience evidence]
- Purpose: [named decision]
- Owner and permission: [owner, basis, confirmer, time]
- Covered population: [population]
- Entity unit: [person, account, opportunity, campaign, or other]
- Time window: [start, end, timezone]
- Input format: [CSV, JSONL, or NDJSON]
- Release mode: [approved mode]
- Minimum cell size: [integer]
- Retention: [source and working-copy actions plus deadline]

| Column | Classification | Used in modeling? | Released? |
|---|---|---:|---:|
| [column] | [one exact class] | [yes/no] | [aggregate only/no] |

### What will happen after approval
[Local audit, suppression, permitted aggregates, and justified optional
modeling for this track.]

### What will not be exposed
[Raw rows, identifiers, sensitive fields, ignored fields, free text, or other
track-specific exclusions.]

### What you will receive before downstream approval
[Openable methodology report, private-data audit, and draft aggregate handoff.]

### File safety
Outputs will be written to a new directory. Existing files and directories are
never overwritten.

**Pre-processing decision:** `ready_for_user_approval` or `blocked`

**Blocking questions:** [questions, or “None”]

Approve this exact intake and classification before I process the rows.
```

If permission, retention, entity unit, or any classification is unresolved,
return `blocked`. Do not process rows in the same response.

For heterogeneous inputs, produce one labeled Response 1 block per track and
require approval of each exact intake. Do not combine CRM and performance rows
under one `data_kind`, column map, or handoff. This runtime does not join raw
files.

## Response 2: Methodology And Draft Approval

After Response 1 is approved, run the preparation and validation loop. Produce:

```markdown
## Private-data evidence ready for review

- Rows examined locally: [count, when permitted]
- Distinct entities: [count, when permitted]
- Release decision: [allowed/blocked]
- Suppression result: [plain-language result]
- Modeling result: [not run, exploratory candidate, or retrospective evaluation]

**Open first:** [data-methodology-report.html]

**Technical files:**
- [private-data-audit.json]
- [draft evidence handoff]

**Approval requested:** approve or reject this exact draft for [allowed use].
No downstream use begins until approval is applied.
```

Do not apply approval in the same response.

## Response 3: Approved Handoff

After the user approves Response 2, apply the supplied approval record and return:

```markdown
## Aggregate evidence approved

- Status: `approved`
- Approved by: [identity]
- Approved at: [time]
- Allowed downstream use: [use]
- Frozen audit hash: [hash]

**Approved handoff:** [file link]
```

## Validation Loop

Run:

```bash
python3 scripts/prepare-private-evidence.py <input.csv|jsonl> <intake.json> <new-output-dir>
python3 scripts/validate-private-evidence.py <new-output-dir>/<draft-handoff.json>
```

If validation fails, fix the intake or deterministic implementation, choose a
new output directory, and rerun both commands. Before Response 2, confirm the
report, audit, and handoff were created together and the handoff's
`source_audit_sha256` matches the exact audit.

After approval, run:

```bash
python3 scripts/approve-private-evidence.py <draft.json> <approval.json> <new-approved.json>
python3 scripts/validate-private-evidence.py <new-approved.json>
```

Fix and repeat until the approved handoff validates. Existing output paths are
never overwritten.

## Worked Example

**Input:** `acme-operations.csv`

```csv
contact_id,email,title,region,industry,buying_stage,annual_value
contact-001,a@example.com,VP Operations,Northeast,Software,Evaluation,950
contact-002,b@example.com,COO,Midwest,Manufacturing,Awareness,420
```

**Approved classification:**

```text
entity_id: contact_id
direct_identifiers: email
quasi_identifiers: title, region
dimensions: industry, buying_stage
metrics: annual_value
```

**Output behavior:**

- `email` and `contact_id` never enter released evidence.
- Rare titles or regions are grouped or suppressed before aggregation.
- A cross-tab below the approved cell minimum returns `count: null`,
  `share: null`, and `suppressed: true`.
- Clusters remain exploratory candidates; they do not become panelists.
- The draft handoff stays unusable downstream until Response 3.

**Shortened audit result:**

```json
{
  "schema_version": "audience-private-data-audit-v1",
  "data_kind": "crm",
  "row_count": 60,
  "entity_count": 60,
  "privacy_risk": {
    "direct_identifier_columns_present": 1,
    "minimum_cell_size": 5,
    "entities_in_rare_quasi_combinations": 1
  },
  "release_readiness": {
    "raw_rows_allowed": false,
    "aggregate_handoff_allowed": true
  },
  "decision": "release_aggregate_evidence"
}
```

**Shortened draft-handoff result:**

```json
{
  "schema_version": "audience-first-party-evidence-v1",
  "package_id": "crm-panel-evidence",
  "status": "draft",
  "covered_population": "Permissioned CRM contacts with buying influence",
  "segment_candidates": {
    "status": "exploratory_candidate_available"
  },
  "approval": {
    "approved_for_downstream_use": false,
    "approved_by": null,
    "approved_at": null,
    "approval_note": null
  }
}
```

These are explanatory excerpts, not standalone contract-valid files. The
runtime outputs the complete strict objects defined in the Contracts reference.
