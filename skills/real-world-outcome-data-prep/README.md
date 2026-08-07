# Real-World Outcome Data Prep

Use Real-World Outcome Data Prep when ads tested by Ad Testing Lab will also run in a real campaign and you want to check the synthetic panel's ranking against the campaign results later.

Before launch, it records the panel's predicted ranking, the exact ads, audience, success metric, campaign dates, and how the ads will be compared. After the campaign, it imports the platform's original results file containing totals by ad and packages it with that record. This prevents anyone from changing the prediction or the success criteria after seeing what happened.

Data Prep does not decide whether the panel was right. A separate Real-World Outcome Validation step performs that comparison.

## Use it when

- You want to record the exact Ad Testing Lab prediction before campaign results exist.
- You want to import aggregate results from a supported social, search, or programmatic ad platform.
- You want to prepare the prediction and campaign results for Real-World Outcome Validation.

## Do not use it for

- CRM, analytics, revenue, retention, or person-level data.
- Deciding whether a panel was right.
- Ranking ads by campaign performance or changing audience profiles to better match the results.
- Publishing, replacing, or editing an audience panel.

## Inputs

Before launch, provide the saved Ad Testing Lab results and campaign plan. After the campaign, provide those approved files again together with the original results files exported from the ad platform.

## Outputs

- A reviewable summary of the prediction and campaign plan recorded before launch.
- Locked copies of every accepted source file, with a new numbered copy each time results are imported.
- Reports showing whether the files are complete enough for Real-World Outcome Validation and whether each campaign result matches the correct ad in the saved Ad Testing Lab results and campaign plan.
- A clear status showing whether validation can begin, required files or details are missing, the results can only be summarized because they were viewed before the plan was recorded, or a privacy, permission, timing, file, or platform-import check failed.

Data Prep stops before the synthetic-versus-real comparison. If someone saw the campaign results before recording the prediction and study plan, the package can describe what happened but cannot count as a preplanned test.

## Start here

- [Prepare and validate with real results](../../docs/guides/validate-with-real-results.md)
- [Calibration and real-world validation](../../docs/concepts/calibration-and-real-world-validation.md)
- [Privacy and data boundaries](../../docs/reference/privacy-and-data-boundaries.md)
- [Outputs and files](../../docs/reference/outputs-and-files.md)
- [Technical skill instructions](SKILL.md)

## Next capability

When Data Prep reports that the package is ready, start Real-World Outcome Validation with that package. Validation compares the panel's saved prediction with the campaign results.
