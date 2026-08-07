# Social Export Mapping Contract

Use this mapping for curated JSON exports such as Apify datasets and raw responses saved from authenticated Sprout, Sprinklr, or other social-listening MCPs. It normalizes provider-specific field paths without baking one actor or MCP schema into the panel methodology.

The mapping JSON contains exactly:

```text
schema_version, batch, records_path, fields, constants
```

`schema_version` is `social-export-mapping-v1`.

`batch` contains exactly:

```text
batch_id, created_at, provider, collector, collector_version,
run_or_dataset_id, query, window_start, window_end, collection_method,
access_route, permitted_use, sort_mode, item_limit, pagination,
completeness, deduplication_control, bot_spam_control
```

`records_path` is a dot-separated path to the array of source records. Use an empty string when the input root is the array.

`fields` contains exactly:

```text
source_item_id, platform, source_url, published_at, unit_of_analysis,
title, text, relevance_score, cluster_id, role_status, author_id, engagement
```

Each scalar value is a dot-separated field path or `null`. `author_id` is optional and is used only to create a run-local salted grouping token for source-concentration checks; the raw identifier is never emitted. `engagement` is an object mapping normalized counter names to dot-separated source paths.

`constants` may provide fallback values for:

```text
platform, unit_of_analysis, role_status, text_fidelity
```

Fictional example. Provider, dates, query, identifiers, and field paths are
illustrative and must be replaced with the approved export's actual metadata:

```json
{
  "schema_version": "social-export-mapping-v1",
  "batch": {
    "batch_id": "workflow-reviews-july",
    "created_at": "2026-07-23T12:00:00Z",
    "provider": "Apify",
    "collector": "approved-review-collector",
    "collector_version": "2026-07-01",
    "run_or_dataset_id": "dataset-123",
    "query": "workflow replacement implementation risk",
    "window_start": "2026-06-23T00:00:00Z",
    "window_end": "2026-07-23T00:00:00Z",
    "collection_method": "curated_export",
    "access_route": "permissioned_api",
    "permitted_use": "audience_research",
    "sort_mode": "newest",
    "item_limit": 200,
    "pagination": "complete_to_limit",
    "completeness": "bounded_by_query_and_limit",
    "deduplication_control": "canonical URL and content hash",
    "bot_spam_control": "collector flag plus manual review"
  },
  "records_path": "items",
  "fields": {
    "source_item_id": "id",
    "platform": "platform",
    "source_url": "url",
    "published_at": "postedAt",
    "unit_of_analysis": null,
    "title": null,
    "text": "content",
    "relevance_score": null,
    "cluster_id": null,
    "role_status": null,
    "author_id": "author.id",
    "engagement": {
      "likes": "engagement.likes",
      "comments": "engagement.comments",
      "shares": "engagement.shares",
      "views": "engagement.views"
    }
  },
  "constants": {
    "platform": "linkedin",
    "unit_of_analysis": "post",
    "role_status": "unknown",
    "text_fidelity": "verbatim_public_text"
  }
}
```

Mapping paths select fields only. They do not run code, JSONPath expressions, filters, or network requests.
