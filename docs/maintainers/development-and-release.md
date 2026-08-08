# Development and release

This page is for maintainers. Product users should start at the [documentation home](../README.md).

## Checkout discipline

Develop on an isolated branch and worktree. The installed live checkout is a runtime source, not a scratch directory. Preserve unrelated dirty work in every repository.

The four connected capabilities share one verified portable runtime even though users invoke them as separate skills. Use the plugin manager's installed copy or one deliberate local checkout. The runtime guard rejects stale, modified, or incomplete release bytes before Real-World Outcome Validation handoffs or other protected operations.

## Dependencies

Use a virtual environment and install only the declared dependency sets needed for the selected tests:

```bash
python3 -m pip install \
  -r skills/audience-ad-testing-lab/requirements-screening.txt \
  -r skills/audience-data-lab/requirements-private-data.txt \
  -r skills/real-world-outcome-data-prep/requirements-outcome-data-prep.txt
```

## Change-specific validation

### Documentation and package surfaces

```bash
python3 -m unittest \
  conformance.test_public_documentation \
  conformance.test_package -v
```

### Audience panel construction

```bash
python3 -m unittest conformance.test_audience_panel_builder -v
```

### Dashboard

```bash
python3 -m unittest conformance.test_dashboard -v
python3 skills/audience-ad-testing-lab/scripts/validate-dashboard.py \
  skills/audience-ad-testing-lab/assets/dashboard-template.html \
  --allow-placeholders
```

### Complete suite

```bash
python3 -m unittest discover -s conformance -p 'test_*.py' -v
```

## Release manifest

The runtime release manifest binds the shipped operational tree. The root `README.md` is excluded because it is the public landing page and is validated by the public-documentation checks instead. Regenerate the manifest after all intended operational changes are complete:

```bash
python3 skills/real-world-outcome-data-prep/scripts/generate-runtime-release-manifest.py \
  --plugin-root . \
  --output skills/real-world-outcome-data-prep/references/runtime-release-manifest.json
```

Never hand-edit the manifest. After regeneration, run the runtime-guard and package tests that authenticate it.

## CI

Fast private-stage validation on pull requests and `main` runs the contract/package lane only. Outcome-data and calibration release gates run from the version-tag release workflow after manifests are regenerated. Workflow contract tests must keep that split closed: heavy gates stay on the tag release path, and the reusable sealed private-stage setup action remains available for those gates.

## Documentation maintenance

Each public fact has one canonical owner:

- user steps: `docs/guides/`;
- meaning and boundaries: `docs/concepts/`;
- volatile methods, outputs, and privacy rules: `docs/reference/`;
- technical runtime authority: `skills/*/SKILL.md` and bundled contracts.

Root and per-skill READMEs remain navigational. When runtime behavior changes, update the canonical public owner and link summaries rather than copying exact thresholds into multiple files.

## Merge and deployment

1. Rebase or merge current `origin/main` before final validation.
2. Regenerate the release manifest from the final tree.
3. Push and open a ready PR with validation evidence.
4. Wait for every required check.
5. Merge only when GitHub reports the PR clean and conflict-free.
6. Fast-forward the canonical live runtime checkout.
7. Delete the completed remote/local branch and remove the worktree.
