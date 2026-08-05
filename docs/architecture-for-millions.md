# Scaling JTorrent search

The original browser-search design loaded every search shard before it could answer a query. That approach becomes expensive long before the corpus reaches millions of records because every visitor pays the bandwidth and memory cost of the entire index.

## Current static design: search v3

The GitHub Pages backend now publishes:

1. **Small status data** at `data/status.json`.
2. **A versioned v3 manifest** at `data/v3/manifest.json`.
3. **Scored token buckets** under `data/v3/tokens/`.
4. **Compact result-document shards** under `data/v3/docs/`.
5. **Source health** at `data/source-health.json`.
6. **A v2 compatibility surface** that reuses the compact v3 documents.

A v3 posting contains:

```text
[doc_id, category_index, source_index, static_score]
```

The frontend can therefore filter category/source postings before hydration, rank without downloading a large candidate set, and request only the shards required for the current 20–25 result window.

The current build policy targets:

- no full-corpus browser download;
- approximately 64 KiB compact document shards;
- no duplicate legacy document corpora;
- an uncompressed GitHub Pages artifact below 70 MiB;
- meaningful queries such as `public domain`, `creative commons`, `Internet Archive`, `Ubuntu ISO`, and `Academic Torrents`.

## Configuration

`config/sources.yml` owns the configured source registry. `config/search-policy.json` owns public-index inclusion and v3 output controls.

Example output policy:

```json
{
  "output": {
    "legacy_shards": false,
    "group_files": false,
    "search_v3": {
      "enabled": true,
      "doc_shard_bytes": 65536,
      "token_bucket_prefix_chars": 2,
      "max_query_tokens": 8,
      "max_tokens_per_record": 32,
      "max_postings_per_token": 50000
    }
  }
}
```

## Practical GitHub Pages limit

Static token buckets and compact shards are appropriate while the artifact remains comfortably inside GitHub Pages build, storage, and bandwidth limits. The frontend must never restore a whole-corpus download path.

For a genuinely multi-million-record index, move query execution and storage to a purpose-built layer, for example:

```text
scheduled collectors
  -> object storage for raw snapshots
  -> search/database index (Typesense, Meilisearch, OpenSearch, PostgreSQL FTS, etc.)
  -> small query API
  -> static JTorrent frontend
```

The public v3 contract provides a clean migration boundary: a future API can preserve the same query/result semantics while changing the storage engine.
