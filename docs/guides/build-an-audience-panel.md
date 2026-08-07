# Build a reusable audience panel

## Use this when

You want an evidence-grounded audience that can be reviewed once, saved, and reused across multiple creative studies. Use [Audience Panel Builder](../../skills/audience-panel-builder/README.md).

If you only need to test finished ads now and have no research, use the [provisional no-research route](build-an-audience-without-research.md) instead.

## What you need

- The audience, market, geography, category, and buying context.
- The decision the audience will support.
- Important exclusions or cohort distinctions that must remain separate.
- Any approved aggregate evidence from Audience Data Lab.
- Any authorized interviews, surveys, reviews, community exports, or research inputs you want considered.

Raw private rows are not accepted. Use [Audience Data Lab](use-private-audience-data.md) first when CRM or person-level data is involved.

## What happens

### 1. Scope the audience decision

The builder locks the audience, decision, evidence basis, market, and exclusions before writing persona prose. It inventories the sources it can actually read; a provider name alone does not prove access.

### 2. Plan the research

The research plan names the questions, source families, collection requirements, and known gaps. For a reusable researched panel, this plan is reviewed before collection so the user can challenge the scope, missing cohorts, or evidence strategy.

### 3. Synthesize evidence

Evidence stays separated by function:

- structural evidence describes the possible population frame;
- surveys and professional research provide recurring findings;
- social, community, forum, and review evidence provide language and context;
- approved aggregate first-party evidence adds owned knowledge;
- historical performance remains context, not proof of future behavior.

Every material finding binds to evidence. Disagreement and missing support stay visible.

### 4. Construct grounded profiles

The builder creates segments and grounded profile snapshots from supported combinations of role, operating context, mindset, and buying situation. It does not create a cross-product of every possible attribute, infer prevalence from source volume, or turn named people into panelists.

These profiles are reusable evidence snapshots. Ad Testing Lab later creates run-specific synthetic executions from them. Read [Profiles, replicates, and people](../concepts/profiles-replicates-and-people.md).

### 5. Review the human-readable panel

The review includes:

- the decision context and audience scope;
- segment definitions;
- grounded profiles and their evidence state;
- needs, objections, behaviors, and creative implications;
- sources and direct links;
- explicit unknowns, disagreements, and limitations;
- the blind construction audit and approval state.

The HTML report is the primary review surface. JSON remains the canonical machine-readable record.

### 6. Package and register only after approval

Reusable packaging is immutable and approval-bound. If a bound brief, panel, evidence file, report, audit, or package changes, downstream approvals become stale. Registration adds a new version; it never overwrites an existing panel.

## What you receive

- Human-readable panel review in HTML and Markdown.
- Audience research report and source inventory.
- Canonical saved-audience JSON.
- Evidence, synthesis, and audit records.
- Immutable ZIP package for authorized transfer or registration.

See [Outputs and files](../reference/outputs-and-files.md) for which files a marketer should open first.

## Reuse and refresh

Before a new ad study, resolve the exact saved panel version against the new scope. A compatible panel is copied into the run as a hash-bound snapshot. A `needs_refresh` or `incompatible` result stops dispatch; update the research and publish a new version rather than editing the old one.

## What this does not establish

A researched synthetic panel is not a representative human sample. Structural weights and evidence improve scope discipline, but they do not convert synthetic feedback into population preference or predicted campaign results. Read [Research and grounding](../concepts/research-and-grounding.md) and [Synthetic evidence and validity](../concepts/synthetic-evidence-and-validity.md).

## Next step

Supply the approved panel package to [Ad Testing Lab](test-ads.md) with the exact creatives you want to screen.
