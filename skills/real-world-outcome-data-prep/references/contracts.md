# Closed Contracts

This reference inventories the strict JSON documents used by Real-World
Outcome Data Prep. The runtime in `scripts/outcome_data_prep/contracts.py` is
authoritative. Unknown keys fail, missing keys fail, schema versions are exact,
timestamps require an offset, and SHA-256 values use
`sha256:<64 lowercase hexadecimal characters>`.

Do not ask the user to fill these documents. Derive them from authenticated
inputs, ask only for non-derivable facts, construct them in code, and let the
shipped validators calculate and verify self-hashes.

## Contents

- [Shared rules](#shared-rules)
- [Prepare Study documents](#prepare-study-documents)
- [Import input and provenance documents](#import-input-and-provenance-documents)
- [Normalized handoff documents](#normalized-handoff-documents)
- [Status derivation](#status-derivation)
- [Durable output layout](#durable-output-layout)

## Shared Rules

- `identifier` means a non-empty canonical runtime identifier accepted by the
  shipped validator.
- `digest` means a prefixed SHA-256 string.
- `timestamp` means timezone-aware ISO 8601.
- `self_hash` fields are `null` during construction and are replaced with the
  digest of the canonical document with that field set to `null`.
- Lists described as non-empty must contain at least one value. IDs that the
  validator marks unique must not repeat.
- Evidence status is exactly `preregistered_holdout`, `descriptive_only`, or
  `blocked`.
- Operational status is exactly `contract_ready`, `incomplete`,
  `descriptive_only`, or `blocked`.
- Adapter maturity is exactly `schema_tested`, `export_verified`, or `blocked`.
- The runtime owns trusted source, chronology, authority, and correction
  fields. Never accept those values merely because a public JSON file asserts
  them.

## Prepare Study Documents

### `outcome-study-setup-v1`

Exact keys:

```text
schema_version: "outcome-study-setup-v1"
study_id: identifier
prepared_at: timestamp
prepared_by: identifier
study_name: string
planned_start_at: timestamp
planned_end_at: timestamp
outcome_access_after: timestamp
primary_metric: string
audience_definition: object
study_setup_sha256: self_hash
```

Dates must be monotone:
`planned_start_at <= planned_end_at <= outcome_access_after`.

The Prepare Study CLI accepts a separate setup wrapper with exactly
`run_root`, `panel_package`, `campaign_plans`, and `supplied_facts`. The wrapper
is an operator input, not a durable contract. `campaign_plans` is a non-empty
list. The skill derives its values and asks only for unresolved facts.

### `outcome-delivery-map-v1`

Exact outer keys:

```text
schema_version: "outcome-delivery-map-v1"
study_id: identifier
registration_id: identifier
sealed_before_outcome_access: boolean
mappings: non-empty list<delivery_mapping>
chronology: chronology
delivery_map_sha256: self_hash
```

Each `delivery_mapping` has exactly:

```text
mapping_id, platform, platform_campaign_id, platform_ad_group_id,
platform_ad_id, platform_creative_id, block_id, study_id, arm_id, batch_id,
segment_ids, creative_id, variant_id, asset_sha256, campaign_plan_sha256
```

All identity fields are identifiers, `segment_ids` is a string list, the two
hash fields are digests, mapping IDs are unique, and each mapping's `study_id`
must equal the outer study ID.

`chronology` has exactly `events`. Each non-empty event has exactly:

```text
event_type: identifier
occurred_at: timestamp
evidence_source_sha256: digest
attested_by: identifier
attested_at: timestamp
authority_id: identifier
```

### `outcome-creative-manifest-v1`

Exact outer keys:

```text
schema_version: "outcome-creative-manifest-v1"
registration_id: identifier
creatives: non-empty list<creative>
creative_manifest_sha256: self_hash
```

Each `creative` has exactly:

```text
creative_id: identifier
variant_id: identifier
asset_sha256: digest
role: string
predicted_rank: non-negative integer
predicted_group: non-negative integer
```

Creative IDs are unique.

### `outcome-registration-receipt-v1`

Exact keys:

```text
schema_version, registration_id, study_id, registered_at, registered_by,
study_setup_sha256, delivery_map_sha256, creative_manifest_sha256,
registration_receipt_sha256
```

The version is `outcome-registration-receipt-v1`; IDs are identifiers,
`registered_at` is a timestamp, the three bound hashes are digests, and the
final field is a self-hash.

### `outcome-registration-receipt-v2`

The authenticated receipt has exactly:

```text
schema_version: "outcome-registration-receipt-v2"
study_id: identifier
registration_id: identifier
registration_sha256: digest
delivery_map_sha256: digest
creative_manifest_sha256: digest
chronology: chronology
evidence_status: evidence_status
receipt_sha256: self_hash excluding both receipt hashes
receipt_hmac_sha256: authority digest
```

The authority HMAC is separate from the receipt self-hash. A self-hash alone
does not establish chronology or authority.

## Import Input And Provenance Documents

### `outcome-source-governance-input-v1`

One uploaded source requires one input with exactly:

```text
schema_version: "outcome-source-governance-input-v1"
data_owner: string
system_of_record: string
permission_reference: string
confirmer: string
allowed_purpose: string
retention_policy: string
minimum_group_size_rule: string
restricted_fields_removed_attestation: boolean
export_method: string
export_timestamp: timestamp
source_governance_input_sha256: self_hash
```

These are source-specific governance facts. Do not reuse one source's answers
for another source unless the user explicitly confirms the same facts for each
file.

### `outcome-source-governance-record-v1`

The public request contains exactly `schema_version`, `governance_input`, and
`source_governance_record_sha256`. The runtime then adds exactly:

```text
observed_minimum_group_size: non-negative integer
protected_staging_location: string
source_filename: string
source_sha256: digest
aggregate_only: true
person_level_data: false
adapter_name: string
adapter_version: string
```

The complete sealed record contains the public request fields plus those eight
trusted fields. The runtime, not the user, derives the source hash, staging
location, observed minimum, privacy result, and adapter identity.

### Strict source context

A named exact adapter context has exactly:

```text
adapter_registration: object
reporting_metadata: object
source_binding: source_binding
```

A generic programmatic context has exactly:

```text
adapter_id: "generic-dsp-mapping-v1"
mapping: object<string source column, string canonical target>
reporting_metadata: object
delivery_map_sha256: digest
source_binding: source_binding
```

Every `source_binding` has exactly:

```text
source_sha256: digest
inventory_sha256: digest
study_id: string
delivery_map_sha256: digest
```

Generate the source context after protected snapshot and inventory. The
generic mapping must be explicit, string-to-string, and one-to-one; it cannot
be inferred after outcome access. Its allowed canonical targets are:

```text
campaign_id, line_item_id, ad_group_id, creative_id, ad_id, date,
impressions, clicks, spend, currency, conversion_value, sample_count,
standard_deviation, exposure_time
```

It requires exactly one conversion source, exactly one currency source, and a
stable `creative_id` or `ad_id`. The operator guide describes how the skill
collects non-derivable reporting facts without exposing this schema to the
user as a form.

### `outcome-source-manifest-v1`

Exact outer keys:

```text
schema_version: "outcome-source-manifest-v1"
source_manifest_id: identifier
study_id: identifier
import_id: identifier
sources: non-empty list<object with unique source_id>
source_manifest_sha256: self_hash
```

The runtime creates the source entries from admitted immutable source files,
their governance records, detection results, and provenance.

### `outcome-correction-request-v1`

Exact keys:

```text
schema_version: "outcome-correction-request-v1"
correction_id: identifier
study_id: identifier
requested_at: timestamp
actor: identifier
reason_code: identifier
reason: string
supersedes_import_id: identifier
supersedes_observation_ids: non-empty list<string>
expected_analytical_identity_sha256: digest
replacement_source_sha256: digest
correction_request_sha256: self_hash
```

The trusted correction context separately binds the authenticated superseded
import (`import_id`, `source_sha256`) and the newly staged replacement source
(`source_manifest_id`, `source_sha256`). A correction may change observed
values and source provenance but not analytical identity.

## Normalized Handoff Documents

### `normalized-outcome-observation-v1`

Exact outer keys:

```text
schema_version, observation_id, study_id, registration_id, import_id,
source_id, source_sha256, source_row_reference, platform, adapter, account,
campaign, ad_group, ad, creative, reporting, attribution, currency, spend,
exposure, outcome, platform_semantics, validation_projection,
normalized_observation_sha256
```

The version is `normalized-outcome-observation-v1`. IDs are identifiers;
source SHA-256 is a digest; source row reference is text. Nested shapes are
closed exactly as follows:

```text
adapter: {adapter_id, adapter_version, maturity}
account|campaign|ad_group|ad|creative: {platform_id}
reporting: {start_date, end_date, timezone, basis, request_level,
  time_increment, segment_grain, latency_state, observed_at}
attribution: {report_time, windows}
currency: {code, basis}
spend: {value, decimal, source_numeric_text, source_metric, source_unit}
exposure: {
  impressions: {value, source_numeric_text},
  clicks: {value, source_numeric_text}
}
outcome: {metric_id, source_metric, value, decimal, source_numeric_text,
  value_state, omitted_zero_behavior}
platform_semantics: {billed_currency, currency_relationship,
  privacy_review_state, demographic_truncation_state, click_semantic,
  optimization_event, delivery_state, skan_state, search_term_id,
  search_term_state}
```

`value_state` is exactly `observed`, `observed_zero`, `null`, `absent`,
`suppressed`, `omitted_zero`, `fractional`, `modeled`, or `estimated`.
Reporting latency is `mature` or `immature`. Numeric source text is preserved;
missing states remain distinct from observed zero.

`validation_projection` has exactly:

```text
status, evidence_status, metric_family, measurement_window,
attribution_window, aggregate, eligible_exposure_count,
missing_outcome_count, effective_sample_size, assignment, confidence_level,
permission_confirmed, outcome_accessed_at, limitations
```

Status is `available` or `unavailable`. Unavailable projections require all
analytical fields to be null. Available projections use one metric family:

```text
binary_proportion aggregate: {success_count, eligible_exposure_count}
continuous_mean aggregate: {sample_count, mean, standard_deviation}
event_rate aggregate: {event_count, exposure_time}
assignment: {design, unit, leakage_detected}
```

This projection prepares unchanged validation input. It does not calculate
comparisons, ordering, uncertainty, eligibility, sufficiency, or claims.

### `outcome-observation-binding-v1`

Exact keys:

```text
schema_version, observation_id, registration_id, registration_sha256,
normalized_observation_sha256, delivery_map_sha256, delivery_mapping_id,
delivery_mapping_sha256, campaign_plan_sha256, platform,
platform_campaign_id, platform_ad_group_id, platform_ad_id,
platform_creative_id, block_id, study_id, arm_id, batch_id, segment_ids,
creative_id, variant_id, asset_sha256, panel_sha256, package_sha256, run_id,
result_sha256, metric_id, measurement_window, attribution_window,
source_sha256, source_row_reference, evidence_status,
observation_binding_sha256
```

IDs are identifiers, listed hashes are digests, `segment_ids` is unique and
sorted, text windows and row references are non-empty, and the mapping digest
must bind the exact delivery-map projection.

### `outcome-prep-readiness-v1`

Exact keys:

```text
schema_version: "outcome-prep-readiness-v1"
study_id: identifier
import_id: identifier
evidence_status: evidence_status
operational_status: operational_status
adapter_maturity: adapter_maturity
reasons: list<string>
readiness_sha256: self_hash
```

### `outcome-import-event-v1`

Exact keys:

```text
schema_version: "outcome-import-event-v1"
import_id: identifier
study_id: identifier
imported_at: timestamp
imported_by: identifier
source_manifest_sha256: digest
observation_ids: non-empty list<string>
import_event_sha256: self_hash
```

Publication wraps this event in the runtime's authenticated append-only ledger
envelope. Never append a bare event or edit a prior line.

## Status Derivation

Derive operational status in this order:

1. Evidence status `blocked` or adapter maturity `blocked` -> `blocked`.
2. Evidence status `descriptive_only` -> `descriptive_only`.
3. Adapter maturity `export_verified` -> `contract_ready`.
4. Otherwise -> `incomplete`.

Therefore `schema_tested` can never produce `contract_ready`. Status is a
runtime result, not a user assertion.

## Durable Output Layout

Prepare Study seals these root files:

```text
study-registration.json
delivery-map.json
study-summary.md
creative-manifest.json
registration-receipt.json
```

Each Import Results generation creates:

```text
imports/<import-id>/source-files/
imports/<import-id>/source-manifest.json
imports/<import-id>/normalized-observations.json
imports/<import-id>/observation-bindings.json
imports/<import-id>/matching-report.md
imports/<import-id>/readiness-report.json
imports/<import-id>/readiness-report.md
```

The study root also carries `import-ledger.jsonl`, authenticated current state,
and privacy-safe rejection receipts. Earlier generations remain immutable.
