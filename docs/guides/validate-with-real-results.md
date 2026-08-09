# Prepare and validate with real campaign results

## Use this when

Use this process when ads tested by Ad Testing Lab will also run in a real campaign and you want to check whether the synthetic panel's ranking matched the campaign results.

[Real-World Outcome Data Prep](../../skills/real-world-outcome-data-prep/README.md) handles the recordkeeping. Before launch, it records the panel's prediction, the exact ads, audience, success metric, campaign dates, and comparison rules. After the campaign, it imports the platform's aggregate results and packages them with the original record. It does not decide whether the panel was correct; Real-World Outcome Validation performs that separate comparison.

The real-study sequence is:

1. Record the panel's prediction and campaign plan before launch.
2. Run the campaign.
3. After confirming you are allowed to use the campaign data, import the original results file exported from the ad platform. It must contain totals by ad, not person-level rows.
4. When Data Prep reports that the package is ready, start Real-World Outcome Validation. That workflow checks the predicted ranking against the campaign results.

## Before launch: prepare the study

1. Open the saved Ad Testing Lab prediction, the ads tested, the audience panel used, and the campaign plan.
2. Confirm that those saved files have not changed, then copy the study details already recorded in them.
3. Add only missing campaign details, such as the success metric, dates, time window for counting results, planned test groups, approver, or the IDs the ad platform assigned to each ad.
4. Review the visible `study-summary.md`.
5. Approve the summary and lock the prediction and plan before anyone views campaign results.

If anyone has already viewed the campaign results, they may be summarized but cannot count as a preplanned test of the panel's ranking. Data Prep records the actual timing and never backdates the plan.

## After the campaign: import results

Upload the approved, locked prediction and campaign plan plus the original results files exported from a supported social, search, or programmatic ad platform. Data Prep keeps a locked copy of every accepted file, checks that it contains no prohibited person-level data, confirms that its columns match a supported platform format, and records every import as a new numbered version.

It reports one of four readiness states:

- `contract_ready`: all required files and checks passed, so Real-World Outcome Validation can begin;
- `incomplete`: a required file or detail is missing or could not be verified, and the report names it;
- `descriptive_only`: someone viewed results before the prediction and plan were locked, so the results can be summarized but cannot independently test the panel's ranking;
- `blocked`: an approval, privacy, timing, file-integrity, or platform-import check failed, and the report names the failure.

## What Data Prep does not do

It does not rank ads using the campaign results, decide whether the panel matched the campaign, propose replacement audience profiles, or edit or publish any panel.

## Separate outcome validation

Real-World Outcome Validation compares the panel's ranking, recorded before launch, with approved aggregate campaign results collected afterward. It checks that the study was recorded before anyone saw results, each campaign result matches the correct ad, enough matched results remain to compare the ads, and the comparison uses the success metric and rules saved before launch.

Validation can report that the panel's ranking matched the campaign results only after the software confirms that the original files have not changed, the plan was recorded before anyone saw results, each result matches the correct ad, enough matched results remain to compare the ads, and the comparison uses the success metric and rules saved before launch. A conclusion that the rankings matched applies only to the exact ranking recorded before launch, and the report states when that conclusion expires. The report separately identifies when the ranking did not match, the evidence was too limited, the study was invalid, or the results can only be summarized. Validation does not predict clicks, conversions, revenue, or lift, and it does not change the original panel, prompts, responses, scores, or test.

Read [Calibration and real-world validation](../concepts/calibration-and-real-world-validation.md).

## Later panel changes

Validation never edits the panel automatically. After repeated authenticated misses from disjoint studies, the experimental real-world panel-calibration route in Audience Panel Builder can diagnose one predeclared persona-behavior hypothesis, create a complete newer candidate, and preserve the exact diff and provenance. The candidate must then pass a fresh, nonoverlapping held-out validation. Registration adds a new version only after explicit human approval of the exact calibration proposal and package; it never overwrites or silently activates the original.

One miss, descriptive outcome feedback, a late explanation, or fictional sandbox data cannot begin this route. The sandbox tests the software mechanics only; it is not evidence that an audience model works in a real market. Even a registered candidate remains explicitly experimental and supports only its narrow validated scope.

### What you do

1. Choose the saved panel and provide or identify the aggregate result exports from the relevant registered studies.
2. Review the one proposed before-and-after persona change if the workflow finds an eligible repeated miss.
3. After the candidate is created, run the planned fresh held-out campaign and provide its aggregate result export.
4. Approve or reject the exact candidate and exact new panel version after the fresh validation is complete.

### What happens automatically

The workflow routes new exports through Outcome Data Prep and Validation. Audience Panel Builder then resolves the authenticated result packages, identifies eligible independent misses, checks alternative campaign explanations, diagnoses at most one supported persona-behavior change, materializes the complete candidate, preserves the diff and provenance, prepares the fresh validation, evaluates the new results, packages the candidate, binds the approvals, and registers the approved new version. You do not create its internal registries, evidence files, hashes, identifiers, authority records, or registration proposal.

The workflow pauses after candidate creation because evidence used to discover a change cannot also validate that change. Fresh held-out results are a real second phase, not another file the system can derive from the original campaigns.

## Next step

When Data Prep reports that the package is ready, start Real-World Outcome Validation with the saved prediction, campaign plan, original results files, and readiness report. If multiple eligible validations show the same bounded miss, ask Audience Panel Builder to improve the saved panel from those results; provide the result exports, not hand-built calibration files. Read [Outputs and files](../reference/outputs-and-files.md) if you need to inspect the files produced at each step.
