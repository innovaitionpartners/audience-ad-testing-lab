# Ad Testing Lab

Ad Testing Lab helps you choose which of 2–100 finished ads are worth testing in a real campaign. It collects multiple separate AI reactions using a saved or one-time set of audience profiles as context. It rejects incomplete or incorrectly formatted responses, then re-scores the ads using different groups of the remaining responses to see whether the same ads keep ranking near the top. The HTML dashboard shows the results and the full AI responses used to produce them. When the same ads keep ranking near the top across the re-scorings, the tool proposes a shortlist; otherwise, it reports that no shortlist is supported.

## Attention heatmaps for ads with imagery

After you approve the shortlist, the tool creates or imports a predicted-attention heatmap for every tested image, carousel card, thumbnail, keyframe, or supplied video frame. The [SUM research model](https://openaccess.thecvf.com/content/WACV2025/html/Hosseini_SUM_Saliency_Unification_through_Mamba_for_Visual_Attention_Modeling_WACV_2025_paper.html) is one external image-analysis system the tool can use to generate these heatmaps. The dashboard shows each original ad image or frame beside its heatmap; warmer areas mean more predicted visual attention.

The marketer records the intended focus before the heatmap is revealed. The report shows whether the predicted hotspots fall on the element the marketer named. It also identifies other hotspots that may draw attention away and named focus elements with little predicted attention.

Heatmaps cannot change the ranking or shortlist. They do not show where people actually looked, record how people responded, or predict clicks, conversions, revenue, or lift. Copy-only tests do not include heatmaps.

## Use it when

- You want to decide which finished ads deserve a live test.
- You need structured AI reactions from a saved or provisional audience.
- You need small-set complete exposure or large-library partial exposure.
- You need post-approval attention evidence for inspectable imagery.

## Do not use it for

- Generating net-new ads.
- Media buying.
- Customer surveys or human-market forecasts.
- Predicting CTR, conversion, pipeline, revenue, or lift.
- Reading raw CRM or customer rows.

## Inputs

Exact finished creatives, campaign context, a saved panel or bounded provisional audience description, the decision and success metric, and inspectable imagery when applicable.

Strategy notes and landing-page fragments are not silently converted into a creative roster.

## Outputs

- Frozen run plan and audience lock.
- Validated synthetic feedback and attempt lineage.
- Deterministic screening, boundary, and finalist records where applicable.
- Attention evidence for imagery.
- Self-contained dashboard and downloadable source exports.

Synthetic executions are not people. The dashboard reports grounded profiles, synthetic executions, accepted feedback records, model calls, and human respondents separately.

## Start here

- [Plain-language guide for marketers](../../docs/guides/marketer-guide.md)
- [Test ads](../../docs/guides/test-ads.md)
- [Test without audience research](../../docs/guides/build-an-audience-without-research.md)
- [Methods and capacity](../../docs/reference/methods-and-capacity.md)
- [Synthetic evidence and validity](../../docs/concepts/synthetic-evidence-and-validity.md)
- [Outputs and files](../../docs/reference/outputs-and-files.md)
- [Technical skill instructions](SKILL.md)

## Next capability

Use the shortlist to plan a real campaign, then [prepare the real study and import aggregate outcomes](../../docs/guides/validate-with-real-results.md).
