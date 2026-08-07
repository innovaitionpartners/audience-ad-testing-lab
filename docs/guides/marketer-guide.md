# Ad Testing Lab for marketers

Ad Testing Lab helps you choose which finished ads are worth spending money to test with real audiences. You provide 2–100 ads and describe who they are for. The tool uses a synthetic audience panel, a set of audience profiles that the AI uses as context, to collect multiple AI reactions and compare the ads. If the same ads remain near the top when the tool re-scores them using different groups of complete, correctly formatted AI responses, it gives you a shortlist for a live campaign test. If they do not, it explains why the test cannot support a shortlist.

A synthetic panel is not a survey group. It is a set of audience profiles that the AI uses as context when reviewing your ads. You can create a reusable panel or a one-time panel used only for the current test. If you already have an approved reusable panel, you can use it again. No human respondents take part.

## What you give it

- The finished ads you want to compare.
- The audience you want to reach.
- The campaign goal, offer, buying situation, and decision you need to make.
- Permission to research a new reusable panel, a plain-language audience description for a one-time panel, or a saved panel you created earlier.

## What you get back

- A plain-language summary of which ads are the strongest candidates for a real test, or why the responses did not support a shortlist.
- Why the AI, when reviewing from each audience profile's perspective, reacted positively or negatively.
- Every AI response kept for the analysis, so you can read the full feedback rather than trust a summary alone.
- A dashboard showing the ads, audience, results, disagreements, methodology, and downloadable source files.
- Predicted-attention heatmaps for images, carousel cards, and video ads supplied as selected frames.

The shortlist is a recommendation for what to test next. It is not a forecast of clicks, conversions, revenue, or lift.

## Where the audience research comes from

A panel uses only sources that match its audience and research questions; it does not automatically use every source below.

| Source type | Examples | What it can add to the panel |
|---|---|---|
| Public population and business data | U.S. Census business data and Bureau of Labor Statistics employment data | Which roles, industries, employer types, company sizes, and locations belong in the audience |
| Published surveys and professional research | Industry surveys, recurring business surveys, and research reports with a visible method | Priorities, needs, buying criteria, and recurring patterns for the population actually studied |
| Current community and market language | Public communities, forums, product reviews, licensed social-listening results, and authorized platform exports | Current questions, objections, workflows, the evidence buyers want before believing a claim, and the words people use to describe them |
| Approved first-party research | Approved summaries of interviews, win/loss research, customer research, sales and support conversations, owned-community discussions, and CRM data checked to remove individual customer details | Context specific to your customers or market without putting raw customer records into the panel |
| Historical performance | Approved aggregate campaign results | Context about what happened previously; it does not prove what a new ad will do |

For each source it uses, the tool records where it came from, whether it may be used, any required permission, its date and research method, its limitations, and a direct link when one exists. If no link exists for a supplied file, the report says that no link was recorded. The tool excludes sources when their origin, allowed use, or required permission cannot be documented. If sources weakly support or disagree about a proposed audience or profile detail, the panel report shows the disagreement. If there is no support, the detail stays unknown or is clearly labeled experimental.

Community posts, reviews, and social engagement can show language and recurring concerns. They are not used to claim that a certain percentage of the market thinks something.

## How research becomes a panel

1. **Define the audience.** The tool records the market, geography, roles, buying situation, exclusions, and audience groups that must remain separate.
2. **Choose research questions and sources.** It maps each research question to sources that can answer it and lists questions for which no adequate source is available.
3. **Check every source.** It reviews whether the source actually matches the audience, is recent enough, explains its method, and is allowed to be used.
4. **Connect findings to sources.** Every need, objection, decision factor, and audience description must link to a source that passed the tool's checks. When sources disagree, the panel report shows both findings.
5. **Build audience profiles.** The tool combines only role, context, mindset, and buying-situation details that the research supports. It does not invent a fully detailed fictional person.
6. **Show you the panel before saving it.** You review the segments, profiles, needs, objections, sources, unknowns, and limitations in a human-readable HTML report.

Once approved, the panel can be saved and reused for later ad tests. The original version is never silently edited; a refresh creates a new version.

## What happens when the ads are tested

The tool creates multiple separate AI review runs from the selected audience profiles. Each run sees the ads in the order assigned for that test and records its reaction without seeing the other runs' responses. The software rejects responses that are incomplete or in the wrong format. It then scores the ads several times using different groups of the remaining responses to see whether the same ads keep ranking near the top. It proposes a shortlist only when that result remains consistent; otherwise, it reports that the test does not support one.

These AI review runs are not additional audience profiles and are not people. The dashboard separately counts audience profiles, AI review runs, responses kept for analysis, requests sent to the AI system, and human respondents.

For 2–6 ads, each AI review run sees every ad. For 7–100 ads, each run sees a planned subset; the assignments vary which ads appear together and where each ad appears before the software calculates the shortlist. The finalists are then compared together in a separate round.

## What the predicted-attention heatmaps show

If the test includes an image, carousel card, thumbnail, keyframe, or supplied video frame, the tool creates or imports a predicted-attention heatmap for each one. Copy-only tests do not include heatmaps.

The [SUM research model](https://openaccess.thecvf.com/content/WACV2025/html/Hosseini_SUM_Saliency_Unification_through_Mamba_for_Visual_Attention_Modeling_WACV_2025_paper.html) is one external image-analysis system that can generate these predicted-attention heatmaps. The tool can instead use another named heatmap generator or a complete heatmap created elsewhere.

Before anyone sees the heatmap, the marketer records the part of the ad that should receive attention, such as the offer, product, proof point, or call to action. The shortlist is approved first. The heatmap is then revealed and compared with that intended focus.

In the dashboard:

- warmer areas mean more predicted visual attention;
- the original ad appears beside the heatmap overlay;
- the report says whether the predicted hotspots fall on the element the marketer identified as the intended focus;
- distracting hotspots and important elements receiving little attention are called out; and
- the provider and limitations are shown.

The heatmap cannot change the AI ranking or shortlist. It is not eye tracking, a record of human attention, a preference vote, or a prediction of clicks or conversions. It is a visual guide that points out parts of the ad to inspect before spending money on a live test.

## Which audience route should you use?

### Research and create a reusable panel

Use this when you expect to reuse the audience across tests or when reviewers need a source list showing how each profile detail was supported. You receive a human-readable panel report and a complete source list.

### Start without research

Use this when you need to test ads immediately. The tool creates a one-time panel using only the audience details you provide, marks missing details unknown, and does not save the panel for reuse.

### Reuse a saved panel

Use this when you previously created an approved audience panel that still matches the market, campaign, and buying situation.

## Where to go next

- [Build a reusable audience panel](build-an-audience-panel.md)
- [Test ads without audience research](build-an-audience-without-research.md)
- [Test a finished set of ads](test-ads.md)
- [Technical and research documentation](../README.md#for-researchers-and-technical-reviewers)
