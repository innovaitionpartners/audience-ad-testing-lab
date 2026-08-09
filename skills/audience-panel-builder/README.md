# Audience Panel Builder

Audience Panel Builder researches and saves a reusable description of the audience that should review your ads. The saved panel contains audience segments and research-supported profiles that Ad Testing Lab can use across multiple creative tests. It can also propose one narrow behavior update when the same saved panel repeatedly ranks ads differently from eligible real campaign results, without changing the original panel.

## Where the research comes from

Depending on the audience, the builder can use:

- U.S. Census and Bureau of Labor Statistics data to define relevant roles, industries, employer types, company sizes, and locations;
- published surveys and professional research to support priorities, needs, and buying criteria;
- public communities, forums, product reviews, results from licensed social-listening tools, and authorized platform exports to capture current language, objections, workflows, and the evidence buyers expect before accepting a claim;
- approved summaries of interviews, win/loss research, customer research, sales and support conversations, owned-community discussions, and CRM data checked to remove individual customer details; and
- historical aggregate campaign results as background, never as proof that a new ad will perform.

Not every panel uses every source type. The research report lists the exact sources used, direct links when available, dates, methods, and limitations. Community posts and social activity can reveal audience language and suggest concerns to test, but they cannot show how widespread an opinion is.

## How research becomes profiles

The builder checks whether each source studies the relevant audience and answers the research question. Every finding used in a profile cites a source that passed those checks, and the panel report lists conflicting findings and unanswered questions. It then combines only role, context, mindset, and buying-situation details that the research supports. Proposed details that lack support are not included in saved profiles; the report lists them separately as unknowns or unproven assumptions to test.

## Use it when

- You need audience segments, buyer mindsets, or grounded profiles supported by research.
- You want one approved audience package that can be reused across creative studies.
- You need to refresh, augment, or audit an existing panel.
- The same saved panel has repeatedly ranked ads differently from eligible real campaign results and you want to evaluate one evidence-supported persona-behavior change against fresh results.
- You have approved aggregate evidence from Audience Data Lab.
- You want the panel to react to or pressure-test ads, newsletter concepts, messages, content, or other creative.

## Do not use it for

- Processing raw CRM or performance rows.
- Changing a panel from one study, descriptive feedback, late creative annotations, or synthetic sandbox evidence.

## Improve an existing panel from real results

Use Experimental Real-World Panel Calibration when an existing saved panel has repeatedly ranked ads differently from eligible real campaign outcomes. The user selects the saved panel and provides or identifies the aggregate campaign-result exports. The surrounding workflow routes any new exports through Real-World Outcome Data Prep and Outcome Validation before Audience Panel Builder receives them. The system handles the rest of the evidence workflow:

1. It resolves the authenticated validation results and looks for the same narrow miss across at least two independent studies of the exact panel version.
2. It checks whether delivery, tracking, targeting, timing, the offer, the landing page, or attribution better explains the mismatch. It asks the user a targeted factual question only when the available evidence cannot resolve one of those causes.
3. If exactly one supported persona-behavior explanation remains, it creates a complete new panel candidate and shows the exact before-and-after change. The candidate is not registered and the original panel remains unchanged.
4. It freezes a new validation plan for the candidate. The user later provides aggregate results from a fresh held-out campaign; the studies used to diagnose the change cannot be reused to evaluate it.
5. If the fresh results support the candidate and every gate passes, it presents the exact change and exact package for human approval. Approval registers a new version without overwriting or silently activating the original.

The user does not assemble validation packages, registries, JSON contracts, hashes, internal identifiers, authority files, candidate folders, or registration proposals. Those are system-managed records.

## Inputs

For a new panel: audience scope, decision, category, geography, buying context, exclusions, research direction, and optional approved aggregate evidence.

For Experimental Real-World Panel Calibration: the saved panel, the relevant aggregate campaign-result exports or registered study references, a later fresh held-out result export, any factual answer needed to rule out a non-panel cause, and the user's final approval or rejection. The workflow routes new exports through the proper outcome-intake and validation steps, then materializes and authenticates every internal calibration record.

## Outputs

- Human-readable audience panel review.
- Research report and source inventory.
- Canonical saved-audience JSON.
- Construction audit and approval bindings.
- Immutable reusable panel package.
- For Experimental Real-World Panel Calibration: an exact diagnosis, bounded proposal, complete versioned candidate, diff and provenance bundle, fresh validation result, and human approval record. Registration is available only when every evidence gate passes.

Grounded profiles are evidence snapshots. Synthetic reactions produced from them are model-generated, not responses from recruited people.

## Start here

- [Plain-language guide for marketers](../../docs/guides/marketer-guide.md)
- [Build a reusable audience panel](../../docs/guides/build-an-audience-panel.md)
- [Research and grounding](../../docs/concepts/research-and-grounding.md)
- [Profiles, replicates, and people](../../docs/concepts/profiles-replicates-and-people.md)
- [Calibration and real-world validation](../../docs/concepts/calibration-and-real-world-validation.md)
- [Outputs and files](../../docs/reference/outputs-and-files.md)
- [Technical skill instructions](SKILL.md)

## Next capability

Supply the approved immutable panel and the exact finished creatives or concepts to generate synthetic reactions.
