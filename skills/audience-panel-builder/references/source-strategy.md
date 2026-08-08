# Source Strategy

## Contents

1. Evidence lanes
2. Question-driven source selection
3. Native recent-social research
4. Authenticated social-listening MCPs
5. Supplied Last30Days JSON adapter
6. Apify and curated-collection adapter
7. Social evidence rules
8. First-party and performance inputs
9. Population source registry v2
10. Source acceptance

## Evidence Lanes

Use each source for the job its collection method can support.

### Structural

Use government and professional data to ground role families, industry, company size, geography, occupational context, and defensible population margins.

Typical sources:

- Census ACS, County Business Patterns, Annual Business Survey, and Business Trends and Outlook Survey;
- BLS employment and expenditure data;
- O*NET occupation, task, skill, and title data;
- official statistical agencies in the target market.

Do not join unrelated datasets into a fictional observed person. Label constructed cells.

### Recurring Surveys And Professional Research

Discover the relevant edition at run time from a stable survey-family page or
search rule. Use transparent surveys and professional research for priorities,
attitudes, goals, pressures, decision criteria, triggers, objections, proof
needs, and media behavior.

The registry stores survey families and discovery rules, not frozen findings or
a promise that the most recent edition is the most relevant one. Verify the
edition, field dates, sample, recruitment, weighting, subgroup cuts, geography,
limitations, and reuse terms before extracting findings. If the registry
freshness status is `review_due`, recheck every selected family link and
methodology page before collection.

### Social, Community, Forum, And Review

Use owned-social exports, licensed social listening, public communities, reviews, forums, and permitted platform data for:

- natural language and phrasing;
- emerging pain points and workarounds;
- questions, objections, skepticism, and proof demands;
- peer, creator, identity, and status signals;
- community and channel norms;
- examples of current workflows.

Do not use social data alone for prevalence, segment size, market share, title incidence, or panel weights.

### First-Party

Use permissioned aggregate interviews, win/loss themes, sales and support themes, customer research, owned-community findings, owned-social analytics, and approved `audience-first-party-evidence-v1` packages for client-specific context.

Never accept raw CRM or performance rows in this skill. Audience Data Lab owns private row-level processing. Never store contacts, handles, transcript identities, account IDs, emails, phone numbers, or individual rows in a reusable panel.

### Performance

Use approved `audience-performance-evidence-v1` aggregate handoffs to identify historical contexts worth representing and to support later calibration. Keep current candidate outcomes hidden from panel construction and response workers.

## Question-Driven Source Selection

The source plan must begin with research questions. Do not require a source lane merely to create the appearance of diversity.

Use these defaults:

- role, industry, company size, and geography: structural sources;
- priorities, needs, and decision criteria: recurring surveys plus relevant first-party research;
- language, live objections, and current platform norms: recent social/community evidence;
- client-specific buying context: first-party evidence;
- panel allocation: observed target margins or clearly labeled planning allocation;
- performance relationship: historical outcomes and later held-out calibration.

Record a visible evidence gap when the relevant lane is unavailable or weak.

## Native Recent-Social Research

This skill must not invoke another skill. Use the current runtime's available web search, browser, public APIs, and authenticated MCPs directly.

For each research question, create platform-appropriate searches for:

- the audience and category;
- the buying trigger or situation;
- current workarounds and alternatives;
- objections, skepticism, and failure stories;
- proof requests, implementation questions, and decision criteria;
- relevant role, industry, community, and review contexts.

Capture the source URL, publication date, retrieval date, platform, query, search window, unit of analysis, short evidence span or faithful summary, and visible engagement metadata. Record which sources completed, returned no results, or failed. Normalize the results through the same `social-observation-batch-v1` contract used by authenticated connectors.

This is a narrow audience-research collector, not a general news, recommendations, comparison, trend-forecasting, or prompting product.

## Authenticated Social-Listening MCPs

At runtime, detect capabilities rather than assuming a vendor. If the user has a Sprout Social, Sprinklr, Brandwatch, Meltwater, Talkwalker, Pulsar, or comparable MCP or authenticated connector, use only the read capabilities verified in `connector-capability-inventory-v1`, such as:

- owned-channel posts, comments, and performance metadata;
- saved listening queries and governed topic definitions;
- client-authorized historical windows;
- platform, account, language, geography, and content-type filters;
- deduplicated or classified exports supplied by the listening platform.

Read the actual tool schema before calling it. A provider name does not establish listening access. Request the evidence semantically and adapt whichever field paths the connector returns. Do not bake one MCP method name or response shape into this skill.

Retain a raw connector response only when permitted and necessary, in the run-private acquisition zone, for the minimum documented period. Exclude DMs and private inbox content by default. Normalize accepted evidence to `social-observation-batch-v1` with a mapping file. Record:

- MCP or connector identity and version when available;
- account or listening-query identity without secrets;
- exact query, filters, date window, timezone, and sort;
- pagination, limits, returned count, and stated completeness;
- provider deduplication, spam, bot, sentiment, and classification behavior;
- whether the data represents owned accounts, earned discussion, reviews, or another corpus;
- access and reuse permission.

An authenticated connector may improve coverage and governance. It does not automatically outrank direct public research, create a probability sample, or give engagement fields prevalence weight.

## Supplied Last30Days JSON Adapter

Last30Days is an optional external handoff, not a nested dependency or an evidence authority. Import it only when the user or an outer orchestration layer has already produced its JSON.

The supplied file must be the stable versioned agent profile:

```text
/last30days <audience-and-decision topic> — return the versioned agent JSON export
```

Accept only the documented `1.x` agent export with:

```text
schema_version, query, generated_at, window_days, source_status,
freshness_verdicts, clusters, results
```

Do not accept `--json-profile=raw`; it is intentionally unversioned and may contain local corpus content. Local corpus evidence is excluded from the stable export by default. If a run explicitly opts local corpus into the export, treat it as permissioned first-party evidence and keep it out of public packages.

Normalize the versioned export with:

```bash
python3 scripts/normalize-social-evidence.py last30days input.json output.json
```

Do not call, locate, or execute the Last30Days skill from inside Audience Panel Builder. If no supplied JSON exists, use native recent-social research or an available authenticated connector.

Use the normalized results as a delta layer:

- durable evidence defines stable role and decision context;
- recent social evidence identifies current vocabulary, news-triggered concerns, and emerging questions;
- a recent spike may create an `emerging_hypothesis`;
- it cannot set a segment weight or silently rewrite a durable archetype.

## Apify And Curated-Collection Adapter

Apify actors and other collectors expose different schemas. Do not hard-code one actor's response shape into the panel methodology.

Export the collected dataset to JSON, create a mapping file using the Social
Export Mapping Contract routed directly from `SKILL.md`, and run:

```bash
python3 scripts/normalize-social-evidence.py mapped dataset.json normalized.json \
  --mapping mapping.json
```

The mapping must record:

- provider and actor or collector name;
- actor build/version and run or dataset ID when available;
- exact query or input;
- collection window and timezone;
- sort mode, limits, pagination, and known completeness;
- source platform;
- access route and permitted use;
- deduplication and bot/spam controls.

A successful collector run proves transport succeeded. It does not establish audience coverage or sampling validity.

Use Apify or another repeatable collector when:

- the source list is intentionally curated;
- collection needs to recur;
- a client supplies an approved actor or dataset;
- the source is unavailable through native research or an authenticated listening connector;
- raw rows need to remain available for a controlled audit.

## Social Evidence Rules

Every normalized observation is data-minimized and omits author identity while retaining:

- platform and canonical source URL;
- publication and collection time;
- query and collection window;
- post, comment, thread, review, or other unit;
- short text excerpt or faithful summary;
- native engagement metadata;
- content hash and source item ID;
- collection method and permission state.

Before synthesis:

1. deduplicate exact and near-identical content, reposts, quoted articles, and cross-posts;
2. separate posts, comments, reviews, reposts, and reactions;
3. cap the influence of one source item or author using a run-local salted grouping token when the provider supplies an identifier; omit the token from the portable package;
4. flag vendor, sponsored, promotional, coordinated, bot, or spam content;
5. keep role status `verified`, `self_reported`, `inferred`, or `unknown`;
6. treat one post as anecdotal regardless of engagement;
7. require independent corroboration for core archetype traits;
8. prohibit `high` confidence when support is social-only.

Engagement may identify material worth reading or provide platform-visibility context. Set its prevalence weight to zero.

## First-Party And Performance Inputs

Accept only approved aggregate evidence handoffs. Verify:

- data owner and permission;
- source-audit and input hashes;
- aggregation and suppression rules;
- extraction date and covered period;
- population or campaign denominator;
- included and excluded records;
- dimensions, metrics, and model status;
- known selection, attribution, and measurement limitations;
- retention and deletion rules;
- approval identity and allowed use.

Use approved aggregate distributions to constrain panel coverage. Treat exploratory clusters only as segment candidates. Do not create synthetic CRM rows as qualitative panelists.

## Population Source Registry V2

Use `audience-source-registry-v2.json` for population-frame source routing. Each
entry declares its exact adapter ID, programs, units, dimensions, observed
joints, geographies, access basis, required capability, authentication mode,
freshness, and implementation import path. The router matches those properties.
It never selects a source from the target-audience name.

Registry evidence bases use the closed Task 3 vocabulary. Adapter loading is a
runtime boundary: the implementation must satisfy the `PopulationAdapter`
protocol and its descriptor must exactly equal the validated registry entry.

Freshness is explicit and deterministic:

- `edition` names the selected release;
- `vintage` names the period represented by the data;
- `published_at` controls whether the release existed as of the frame request;
- a release published after the request's `as_of` date is unavailable; and
- the plan returns the exact freshness object used for every selection.

Route failures are terminal and specific. Do not substitute another source when
the required capability or authentication is unavailable, the geography is
unsupported, the unit is incompatible, a required dimension is absent, or a
critical joint is missing. A source that covers two marginals does not cover
their joint. Routing tests deterministic compatible-source combinations before
reporting a missing dimension or joint; two same-unit adapters may be selected
when their combined declared coverage is required.

The initial public adapters are pinned to BLS OEWS May 2025, Census SUSB 2022,
and Census CBP 2023. Their conformance lane reads committed source-neutral
snapshots and verifies canonical hashes. Live acquisition belongs only in an
explicit integration route. Population conformance must not call a network.

Keep source units and denominators intact:

- OEWS employment estimates are `persons` and exclude self-employed workers;
- SUSB firm counts are `firms` with an `employer-firms` denominator; and
- CBP establishment counts are `establishments` with an
  `employer-establishments` denominator.

These sources may inform one frame, but their counts are not interchangeable and
must not be reconciled into one denominator.

The authorized Audience Data Lab adapter accepts only the canonical Task 2
handoff plus its canonical output directory. It delegates handoff and output
hash validation to the authoritative Data Lab validator, reads only the
hash-bound structural-frame output, and never reopens original client source
files. The approved aggregate adapter delegates to the existing strict Data Lab
handoff validator and consumes only approved
`audience-first-party-evidence-v1` distributions or cross-tabs. Performance
evidence remains in the separate calibration lane and is not a population
source program. Drafts, unauthorized uses, raw rows, and arbitrary source paths
are not adapter inputs.

Every normalized public or aggregate result must satisfy
`audience-frame-observation-batch-v1`; the shared finish boundary calls the
authoritative Task 3 validator before returning. Use a published Census noise
flag only when it belongs to the selected measure; an employment noise flag
must not be attached to a firm or establishment count. A published count with
no interval uses equal point bounds and an explicit no-interval method for that
table universe. Suppressed cells retain null estimates and paired null
uncertainty bounds with a nonempty method; they are never rewritten as observed
zero. An exact approved-cohort aggregate may use equal lower and upper point
bounds only when the method states that the value is exact for that covered
cohort, not a market sampling interval.

## Source Acceptance

Run `score-research-sources.py`. The scorer uses declared assessment states for:

- audience match;
- decision match;
- methodology transparency;
- collection quality;
- recency;
- geography match;
- useful subgroup cuts;
- permitted use.

Permission marked `prohibited` or `unknown` fails. A source with a high numeric
score may still be rejected when it lacks exact provenance, duplicates an
upstream source, would derive a sensitive attribute from raw or person-level
data, or leaks candidate-creative knowledge. Evidence-grounded synthetic
sensitive-audience concepts remain permitted.

Source count never substitutes for direct relevance.
