# Methods and capacity

Ad Testing Lab uses two screening designs. The creative count determines the route; the audience structure and design determine synthetic capacity.

## Complete exposure: two to six creatives

Every synthetic execution reviews the complete creative set using progressive reveal and returns a complete ranking.

Both routes are executable. Current v3 complete-exposure studies use dynamic profile-aware capacity rather than the frozen v2 segment multiplier.

### Current v3 runs

The planner chooses the smallest frozen-weight-compatible allocation that:

- represents every grounded profile according to the resolved panel;
- meets the experimental per-profile usable-feedback floor;
- preserves balanced reserve blocks;
- supports required profile and archetype sensitivity where applicable;
- reserves finalist work separately;
- fits within the authorized ceiling.

The result is stratified and bootstrapped within grounded profile, then summarized using the frozen profile and segment weights. Capacity is not chosen from segment count alone.

An honestly single-profile provisional scope can mark leave-one-profile and leave-one-archetype sensitivity as not applicable. Its result remains conditional on that single modeled context.

### Frozen v2 runs

Existing frozen v2 manifests retain their original nine-planned/eight-usable per-segment policy. That compatibility rule does not define capacity for new v3 runs.

## Partial exposure: seven to 100 creatives

After a burden pilot passes, each synthetic execution reviews a deterministic near-balanced four-ad block and identifies the strongest and weakest option.

For v3 audiences, the assignment must jointly balance creative exposure and grounded-profile representation. The comparison graph must remain connected under required profile-removal checks, and each profile must meet its frozen participation and usable-record floors.

A failed or unrun burden pilot returns `split_required`. If the planner cannot create a profile-conditioned design within the authorized ceiling, it requests more capacity or a narrower library rather than falling back to a weaker segment-only plan.

## Boundary work

Complete exposure has no boundary stage. Partial exposure may use a separate frozen pairwise boundary wave only when the valid first-round result identifies an unclear cutoff group.

The boundary model remains on its own scale. It cannot be pooled with first-round utility. An unresolved cutoff is an acceptable refusal state.

## Finalist work

Finalist capacity is reserved before screening and cannot be spent elsewhere. Before human approval, finalist metrics remain empty. After approval, fresh synthetic executions review the complete approved finalist set.

## What the ceiling means

The historical field `maximum_synthetic_panelists` means the maximum unique synthetic-replicate/job slots authorized across screening, boundary, and finalist stages. It is not a provider/model-call ceiling.

Progressive-reveal stages, retries, and rejected attempts increase `total_model_calls` but do not consume another unique job slot.

## Why there is no universal sample-size formula

Synthetic executions are not human survey respondents. Capacity is a design and recovery requirement for one model-conditional protocol. It depends on:

- creative count and method;
- grounded-profile weights;
- balanced coverage;
- usable-feedback floors;
- graph connectivity for partial exposure;
- stability and disagreement gates;
- applicable sensitivity checks;
- frozen reserves and finalist capacity.

These controls support conditional run recoverability. They do not establish human-sample adequacy or population uncertainty.

## Validity and thresholds

The shipped configuration owns exact numerical thresholds. Public summaries should link here rather than duplicate volatile values. A closed run is `valid` only when every required calibrated gate passes; otherwise it may be `exploratory`, `invalid`, or `incomplete`.

## Related documentation

- [Test ads](../guides/test-ads.md)
- [Profiles, replicates, and people](../concepts/profiles-replicates-and-people.md)
- [Synthetic evidence and validity](../concepts/synthetic-evidence-and-validity.md)
- Technical operators: [`Ad Testing Lab SKILL.md`](../../skills/audience-ad-testing-lab/SKILL.md)
