# Contributing

Audience Ad Testing Lab is a multi-skill plugin with closed runtime contracts, immutable package formats, and co-shipped release manifests. Documentation and runtime changes must preserve those boundaries.

## Development setup

- Python 3.11+
- NumPy and SciPy for screening
- OpenPyXL for private-data and outcome spreadsheet support
- An Agent Skills-compatible runtime for full workflow testing

Install all declared dependencies from the repository root:

```bash
python3 -m pip install \
  -r skills/audience-ad-testing-lab/requirements-screening.txt \
  -r skills/audience-data-lab/requirements-private-data.txt \
  -r skills/real-world-outcome-data-prep/requirements-outcome-data-prep.txt
```

Use a virtual environment. Do not modify a system-managed Python installation.

## Repository boundaries

- Runtime instructions and code live under `skills/`.
- Public user documentation lives under `docs/`; private implementation history remains outside this repository.
- Conformance tests live under `conformance/`.
- Private input data, proprietary calibration data, optimizer history, and person-level evidence remain outside the repository.
- Do not add maintainer-specific absolute paths to public files.

## Public validation

Run the focused package and documentation checks first:

```bash
python3 -m unittest conformance.test_package -v
python3 -m unittest conformance.test_public_documentation -v
```

Run the broader conformance suite for runtime changes:

```bash
python3 -m unittest discover -s conformance -p 'test_*.py' -v
```

Dashboard changes also require:

```bash
python3 skills/audience-ad-testing-lab/scripts/validate-dashboard.py \
  skills/audience-ad-testing-lab/assets/dashboard-template.html \
  --allow-placeholders
```

## Release identity

Any tracked byte change requires regeneration of the co-shipped runtime release manifest before CI can pass. Use the repository generator rather than editing the manifest manually.

See [Development and release](docs/maintainers/development-and-release.md) for the current command and release checklist.

## Documentation changes

- Root and per-skill READMEs summarize and link; canonical detail belongs in one guide, concept page, or reference.
- Check all relative links and images.
- Audit volatile claims against current `SKILL.md` and runtime policies.
- Distinguish v3 behavior from frozen v2 compatibility.
- Never call synthetic executions people, customers, consumers, respondents, or a market sample.
- Never publish private or unsupported example material.

## Pull requests

Explain what changed, why, user impact, validation, and any release-manifest update. Required CI must pass before merge.
