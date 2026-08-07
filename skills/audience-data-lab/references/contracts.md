# Audience Data Lab Contracts

## Contents

1. Intake
2. Authorized source profile
3. Authorized mapping and transformation
4. Audit
5. First-party evidence
6. Performance evidence
7. Approval

Unknown fields fail.

## Authorized Source Profile

`authorized-audience-source-profile-v1` contains exactly:

```text
schema_version, profile_id, profile_version, profiled_at, bundle_sha256,
inputs, tables, relationships, candidate_semantics, privacy_risk, unresolved,
decision
```

`decision` contains exactly:

```text
status, allowed_next_route, reasons
```

The allowed status and route pairs are:

```text
ready_for_mapping              -> aggregate_transform
needs_clarification            -> aggregate_transform
requires_private_aggregation  -> private_aggregation
rejected                       -> none
```

`inputs` records normalized source display name, format, byte count, SHA-256,
and workbook metadata. `tables` records the file, sheet or JSON record path,
shape (`wide`, `long`, `nested`, `relational`, or `canonical`), row and column
counts, field names, observed scalar types, null rates, sample-safe value
classes, and candidate units, denominators, and field roles.

The profile is a structural safety boundary, not a mapping or transformation
contract. It must not include a raw person value, row, source path, source
sample, inferred ambiguous semantic, de-identification result, or file join.
Candidate semantics require confirmation. Privacy risk routes the bundle to
private aggregation; it never attempts de-identification in place.

CSV inspection requires UTF-8 or UTF-8-SIG and an explicit detected delimiter.
JSON inspection accepts one object or array and records declared record paths.
It uses one-pass bounded structural inspection: it retains only the current
record and aggregate summaries, not a record array. It enforces the declared
table row and column limits while preserving supported nested record paths.
XLSX inspection uses read-only, non-formula-evaluating, link-disabled loading.
Formula cells, external links, VBA/macro-enabled content, encryption, `.xls`,
`.xlsm`, and ZIP data that is not a validated `.xlsx` are rejected.

## Authorized Mapping And Transformation

The only direct aggregate route is:

```text
authorized-audience-source-profile-v1
→ reviewed authorized-audience-mapping-v1
→ deterministic transformation
→ authorized-audience-handoff-v1
```

The source profile must allow `aggregate_transform`: `ready_for_mapping`, or
`needs_clarification` whose exact unit, denominator, join, and field questions
are resolved in the approved mapping. A profile with person-level risk takes
the private-aggregation route and cannot be made eligible by dropping or
renaming fields in a mapping. Eligibility is recomputed from the validated
profile: nonempty `privacy_risk`, nonempty `unresolved`, or an empty table list
fails even if the decision block claims an eligible route.

`authorized-audience-mapping-v1` contains exactly:

```text
schema_version, mapping_id, mapping_version, source_profile_sha256,
input_hashes, selections, operations, field_routes, expected_outputs,
ignored_fields, privacy_requirements, approval
```

Each selection contains exactly:

```text
selection_id, file, file_sha256, sheet, record_path, fields, unit,
denominator, aggregate_join_keys
```

Each `expected_outputs` entry contains exactly:

```text
dataset, route, filename, schema_version, metadata
```

`metadata` is a strict, route-specific construction contract. It names every
canonical identifier, provenance value, permission state, source-field
binding, and uncertainty or outcome semantic needed by the authoritative
downstream schema. The transformer never infers those semantics from source
column names or values. Frame cell metadata, structured-evidence item
metadata, and social-observation metadata must exactly cover the transformed
records. Each outcome document must declare a unique `feedback_id` and an
exact, unique `record_match` that selects one transformed aggregate row; emit
one canonical outcome document per matched row.

The file, hash, sheet, record path, and fields must resolve exactly to the
approved source profile. Unit must identify an aggregate unit, and denominator
must be resolved, not `unknown`, `ambiguous`, or inferred. Every profiled table
and field must be accounted for exactly once: selected or represented by one
`ignored_fields` entry containing `file`, `sheet`, `record_path`, `field`, and
`reason`.

Operations are strict tagged objects selected from:

```text
select, rename, cast, flatten, wide_to_long, pivot, join, category_map,
normalize_missing, normalize_suppression, derive_share, normalize_weight,
aggregate, filter, sort
```

`cast` allows only `string`, `integer`, `number`, `boolean`, and `date`.
`filter` allows only `equals`, `not_equals`, `in`, `not_in`, `is_null`, and
`is_not_null`. `join` uses only exact approved aggregate keys and
`one_to_one` or `many_to_one`; duplicate keys fail. `pivot` declares every
allowed output column, and an undeclared category fails. Category mappings
declare whether an unmapped value is an error, retained, or set to null.

No operation accepts an expression, query, template, function, callable,
script, fuzzy key, dynamic import, or generated code. Every expected output
field receives exactly one of:

```text
structural_frame, overlay_evidence, profile_seed, outcome_feedback, unsupported
```

Every selected and operation-derived dataset field receives exactly one route,
not only fields present in the final output. A field marked `unsupported` must
be explicitly removed by a declared `select`; it cannot survive to an output.
Routes propagate through every operation. The route must match its complete
output, and evidence ancestry cannot become structural count, share, rate,
percentage, weight, denominator, population, or estimate through rename,
derive, normalization, aggregation, or another transformation. Overlay and
profile-seed evidence never supplies structural frame weight.

A dotted `flatten` source path inherits provenance from its selected top-level
field. For example, `identity.segment` is bound to selected field `identity`;
an absent top-level field fails mapping validation.

`privacy_requirements` contains exactly:

```text
permission_confirmed, aggregate_only, minimum_cell_size,
prohibited_routes, resolved_clarifications
```

`resolved_clarifications` must exactly cover the eligible clarification codes
in `decision.reasons` and may contain only the supported unit, denominator,
join, field meaning, or combined unit-and-denominator confirmations. Nonempty
profile `unresolved` issues remain blocking. Count-like structural values below
`minimum_cell_size` fail; suppression must be explicit rather than represented
as numeric zero.

Approval contains exactly:

```text
status, approved_by, approved_at, mapping_sha256
```

`status` must be `approved`. Compute `mapping_sha256` from canonical JSON with
`approval.mapping_sha256` temporarily set to `null`, then store that digest in
the approved mapping.

Canonical output names are:

```text
frame-observations-0001.json
structured-evidence-0001.json
social-observations-0001.json
profile-seeds-0001.json
outcome-feedback-0001.json
```

Additional route batches increment the four-digit suffix. A profile-seed file
is only a reviewed evidence input; it is not a reusable profile until the
Panel Builder construction evidence review accepts it.

Canonical route/schema pairs are fixed:

```text
frame-observations     -> structural_frame / audience-frame-observation-batch-v1
structured-evidence    -> overlay_evidence / audience-structured-evidence-batch-v1
social-observations    -> overlay_evidence / social-observation-batch-v1
profile-seeds          -> profile_seed / audience-profile-seed-batch-v1
outcome-feedback       -> outcome_feedback / panel-outcome-feedback-v1
```

The filename family, semantic route, and schema version must match this
registry. These files are the authoritative downstream documents, not generic
route envelopes. Frame observations and outcome feedback must pass the Ad
Testing Lab canonical validators. Structured and social evidence must each
pass its dedicated Panel Builder evidence import path. Validation runs before
serialization, hashing, reporting, writing, and handoff publication; handoff
validation repeats it against the published bytes. Canonical documents have
exact route-specific schemas. Unknown fields, prohibited identifier names,
semantic mismatch, or non-canonical JSON fail.

`authorized-audience-transformation-report-v1` binds the exact source profile,
mapping, transformer version, every input hash, and every output hash. It
records source reads, operations, joins and reshapes, field changes, ignored
and dropped fields, category handling, missing and suppressed values, rejected
and filtered values, source units and denominators, route counts, loss and
coverage consequences, warnings, blocking errors, and status:

```text
complete, complete_with_loss, blocked
```

Suppression is represented separately from numeric zero. A completed report
cannot contain blocking errors. A blocked report must contain them. Nested
operation details are strict tagged objects, and summaries must reconcile
exactly with source reads, operation logs, outputs, route counts, and reported
loss consequences. Every tagged detail value is type- and enum-validated, its
declared fields must match the approved mapping, and coverage is exactly
`all_selected_rows_and_fields_preserved` or
`all_loss_and_coverage_consequences_reported` as determined by consequences.

`authorized-audience-handoff-v1` binds the source-profile hash, approved
mapping hash, transformation-report path and hash, canonical output paths and
hashes, profile-seed references, and aggregate privacy decision. The handoff
references exact canonical copies named `approved-source-profile.json` and
`approved-mapping.json`; validation loads and validates both copies as well as
re-hashing them. Paths are single relative canonical filenames. Validation
re-hashes every referenced file under the supplied output root, reruns the
authoritative route validator for each canonical output, and rejects path
traversal, missing files, semantic mismatch, non-canonical serialization, or
hash mismatch.

Each selected source is opened once as a binary snapshot. The transformer
hashes and parses those exact bytes, then compares file identity, size, and
modification time before and after the read; a concurrent mutation fails. It
builds and validates every output in memory, writes a new sibling staging
directory, validates the complete handoff there, and publishes with one rename.
The rename uses the supported POSIX atomic no-replace primitive, so a
destination created after preflight is not replaced. Any write,
final-validation, or publication failure removes the staging directory. The
final output path must not already exist and is never overwritten.

## Intake

`audience-private-data-intake-v1` contains exactly:

```text
schema_version, project_id, created_at, data_kind, purpose,
covered_population, time_window, permission, columns, privacy,
analysis, allowed_uses, prohibited_uses, retention
```

`data_kind` is:

```text
crm, customer, sales, product_usage, performance
```

`time_window` contains:

```text
start, end, timezone
```

`permission` contains:

```text
confirmed, confirmed_by, confirmed_at, data_owner, legal_or_contract_basis,
note
```

`columns` contains:

```text
entity_id, direct_identifiers, quasi_identifiers, sensitive, dimensions,
metrics, outcome, event_date, ignored
```

Every input column must appear exactly once. `outcome` and `event_date` are strings or `null`.

`privacy` contains:

```text
minimum_cell_size, release_mode, privacy_budget_epsilon,
suppress_rare_values, allow_synthetic_release
```

`release_mode` is:

```text
aggregate_only, k_anonymous, differential_privacy, synthetic_tabular
```

The core deterministic path supports `aggregate_only` and `k_anonymous`. Differential-privacy and synthetic-tabular releases require a separately recorded engine, parameters, privacy assessment, and utility assessment.

`analysis` contains:

```text
generate_cross_tabs, max_cross_tab_dimensions, modeling_mode,
feature_columns, cluster_counts, model_seed, minimum_model_rows,
temporal_holdout_fraction
```

`modeling_mode` is:

```text
none, segment_candidates, performance_prediction
```

`retention` contains:

```text
raw_input_action, working_copy_action, deadline, approved_by
```

Allowed actions are `retain_in_place`, `return_to_owner`, and `delete_working_copy`. The skill never deletes the user’s source file.

## Audit

`audience-private-data-audit-v1` contains exactly:

```text
schema_version, project_id, generated_at, input_sha256, data_kind,
row_count, entity_count, column_inventory, missingness, privacy_risk,
analysis_readiness, release_readiness, retention, decision, reasons
```

It contains no row samples, raw values, rare combinations, file paths, or identifiers.

## First-Party Evidence

`audience-first-party-evidence-v1` contains exactly:

```text
schema_version, package_id, created_at, status, source_audit_sha256,
input_sha256, purpose, covered_population, time_window, evidence_basis,
data_quality, distributions, cross_tabs, segment_candidates,
privacy_assessment, allowed_uses, prohibited_uses, limitations, approval
```

Every distribution or cross-tab cell contains:

```text
dimensions, count, share, suppressed
```

Suppressed cells set `count` and `share` to `null` and do not include original rare values.

`segment_candidates` are exploratory model outputs. They cannot be used as approved panel segments without Audience Panel Builder research support and approval.

## Performance Evidence

`audience-performance-evidence-v1` contains exactly:

```text
schema_version, package_id, created_at, status, source_audit_sha256,
input_sha256, purpose, covered_population, time_window, evidence_basis,
outcome_definition, data_quality, cohort_results, temporal_split,
model_results, calibration_scope, privacy_assessment, allowed_uses,
prohibited_uses, limitations, approval
```

`temporal_split` records the exact train and holdout boundaries. `model_results` separates baseline and model metrics and always uses:

```text
validation_state: not_run | insufficient_data | retrospectively_evaluated
```

It cannot emit holdout or prospective validation claims.

## Approval

Every handoff uses:

```text
approval: {
  approved_for_downstream_use,
  approved_by,
  approved_at,
  approval_note
}
```

Draft generation sets `approved_for_downstream_use` to `false` and approval identity and time to `null`. Only a user-supplied approval record may change those fields.

`audience-evidence-approval-v1` contains exactly:

```text
schema_version, approved_for_downstream_use, approved_by, approved_at,
approval_note
```

The approval script deep-copies the draft and permits changes only to top-level `status` and `approval`. It canonical-JSON compares every other field before writing the approved handoff.
