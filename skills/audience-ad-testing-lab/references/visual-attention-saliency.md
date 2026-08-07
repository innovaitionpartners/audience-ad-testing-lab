# Automatic Attention-Heatmap Evidence

Use this reference for every study with inspectable imagery. Attention heatmaps are a required post-approval diagnostic lane, not a user-selected add-on.

## Coverage Rule

Automatically generate or import one attention heatmap for every inspectable media representation in:

- `static_image`;
- every card in `carousel`; and
- every supplied thumbnail, keyframe, or video-frame representation in `video_representation`.

`copy_only` is the sole normal omission route. It contains no imagery evidence, renders no heatmap tab, and states **No imagery was tested.**

For imagery, a missing original, overlay, representation binding, hash, provider/method field, strict timestamp, categorical alignment, or limitation is a hard stop before dashboard rendering. Do not silently omit the tab or downgrade the evidence state.

## Evidence Boundary

Saliency predicts visual prominence under a computational model or preserves a supplied heatmap from another documented provider. It is downstream diagnostic evidence only.

It cannot change:

- screening math;
- boundary resolution;
- finalist shares;
- rubric scores; or
- the deterministically proposed roster or approved finalist roster.

It is not eye tracking, comprehension, relevance, credibility, offer appeal, model preference, thumb-stop, watch-through, CTR, conversion, pipeline, revenue, or human behavior.

A manual visual observation may be retained as a clearly labeled heuristic outside evidence scoring. It cannot satisfy the heatmap contract and cannot be represented as an evidence-provider equivalent.

## Timing

The intended attention target is declared before any heatmap or overlay is revealed. The deterministically proposed finalist roster is approved before reveal. Both comparisons are strict:

```text
approved_at < revealed_at
target_declared_at < revealed_at
```

Use timezone-aware ISO 8601 timestamps. Equality fails.

Do not show an overlay to any synthetic response worker. Original-creative reactions, screening, boundary resolution, roster approval, and finalist scoring are complete before reveal.

If a person changes the roster after reveal, preserve:

- `roster_decision.status: approved_with_override`;
- `changed_after_saliency_reveal: true`;
- a nonempty override reason; and
- the dashboard label `saliency-informed human override`.

Never rewrite the deterministically proposed roster to make it appear the heatmap caused the original result.

## Representation Binding

Every media representation in `creative-roster.json` has a stable, unique `representation_id` and original `content_hash`. The saliency entry uses the same `variation_id`, `representation_id`, and `content_hash` and adds an `overlay_content_hash` for the exact overlay bytes.

Required conditions:

- Original and overlay paths resolve inside the run directory.
- Both files use renderable image MIME types.
- `content_hash` equals `sha256:` plus the SHA-256 of the original bytes.
- `overlay_content_hash` equals `sha256:` plus the SHA-256 of the overlay bytes.
- The saliency entry’s `provider` equals the index provider.
- The set of saliency `representation_id` values exactly equals the set of tested media representations: no missing, duplicate, or unknown IDs.

## `saliency-index.json`

```yaml
study_id: ""
status: available
provider: SUM | imported_heatmap | another_computational_provider
method: ""
revealed_at: "2026-07-22T15:05:00Z"
entries:
  - variation_id: creative-a
    representation_id: creative-a-static-01
    content_hash: sha256:<original-hex>
    original_path: media/creative-a.png
    overlay_path: media/creative-a-overlay.png
    overlay_content_hash: sha256:<overlay-hex>
    predeclared_target: "Offer and CTA"
    target_declared_at: "2026-07-22T15:00:00Z"
    categorical_alignment: aligned | partially_aligned | misaligned | unclear
    provider: SUM
    limitations:
      - "Predicted visual prominence is not eye tracking."
```

`limitations` is a nonempty array for every representation. Use categorical alignment only; do not invent a numeric alignment score or fold alignment into any synthetic-response-derived measure.

## Provider Routes

### Generate With SUM

SUM is one external computational provider. When a prepared checkout and model are available, run one call per representation:

```bash
python3 scripts/run-sum-saliency.py \
  --img-path /absolute/path/to/representation.png \
  --output-dir /absolute/path/to/run/media \
  --condition 2 \
  --heat-map-type Overlay \
  --sum-repo /absolute/path/to/SUM
```

Use the closest declared content condition:

| Condition | Use |
|---:|---|
| `0` | Natural scenes using the mouse-data condition. |
| `1` | Natural scenes using the eye-tracking-data condition. |
| `2` | Commercial, product, offer, retail, display, or ad-like imagery. |
| `3` | Landing pages, product UI, app screens, or UI-heavy creative. |

The wrapper’s success exit does not by itself satisfy this contract. Verify that the expected original/overlay files exist and pass all index and hash checks. Any provider failure is blocking until another computational provider or a complete imported heatmap is supplied.

### Import Existing Evidence

Imported evidence is acceptable when the provider and method are named, exact original and overlay bytes are present, and all fields/timing rules pass. Do not import an overlay if its original does not hash-match the tested representation.

## Interpretation

For each representation, answer only:

- Where is predicted attention most likely to cluster?
- Does that area align categorically with the predeclared target?
- Which offer, proof, CTA, product, face, brand cue, or focal subject appears prominent?
- Which distracting hotspot or missed element should a marketer inspect?
- What provider/content limitations constrain the read?

Preserve conflicts. A creative may direct attention to the intended area and still receive weak synthetic reactions because the message or offer is unconvincing. The reverse can also occur.

## Dashboard Requirements

The **Attention heatmap** tab shows every representation in a large, inspectable original/overlay comparison with:

- creative display name and representation label;
- **Original ad** and **Predicted attention**;
- the explanation **Warmer areas = more predicted attention**;
- side-by-side original and predicted-attention views;
- provider and method;
- predeclared target and target timestamp;
- reveal timestamp and approval state;
- categorical alignment;
- nonempty limitations; and
- accessible alt text and keyboard controls.

The Methodology view discloses hashes, provenance, timing, coverage, and the rule that heatmaps cannot alter synthetic-response-derived results.

## Validation Checklist

- Study format is canonical.
- `copy_only` has no saliency index and says **No imagery was tested.**
- Every imagery representation has exactly one saliency entry.
- Every original and overlay resolves and uses a renderable image MIME type.
- Original and overlay hashes match the actual bytes.
- Provider and method are nonempty; entry provider matches index provider.
- `approved_at < revealed_at` and `target_declared_at < revealed_at` with timezone-aware timestamps.
- Categorical alignment is canonical.
- Limitations are nonempty for every entry.
- No prompt, score, shortlist, boundary, share, or roster was changed after heatmap generation.
- Any post-reveal roster change is a labeled human override.
