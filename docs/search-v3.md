# JTorrent search contract v3

> Historical reference only. Production output is v4-only and no longer publishes this compatibility contract.

The public backend now publishes a compact v3 static search index while retaining a lightweight v2 compatibility surface for the currently deployed frontend.

## Public endpoints

- `data/status.json` — small status document for the homepage.
- `data/v3/manifest.json` — v3 search contract, categories, sources, token buckets, and compact document shards.
- `data/v3/tokens/<prefix>.json` — postings encoded as `[doc_id, category_index, source_index, static_score]`.
- `data/v3/docs/*.json` — compact result documents capped near 256 KiB per shard.
- `data/source-health.json` — source counts, failures, and policy-disabled sources.
- `data/v2/manifest.json` and `data/v2/tokens/*.json` — compatibility data that reuses v3 document shards.

The old full, compact, search, category, and source duplicate shards are removed after generation. The legacy `search-index*.json` endpoints remain small pointers.

## Query behavior

Tokenization keeps meaningful domain words such as `public`, `domain`, `creative`, `commons`, `archive`, `official`, `torrent`, `download`, and `metadata`. Only grammatical/high-frequency words are removed.

The frontend should:

1. Fetch `data/status.json`.
2. Fetch `data/v3/manifest.json` when `search_contract` is `v3`.
3. Tokenize with the manifest stopword list and token limit.
4. Fetch only the required token buckets.
5. Apply category/source filters to postings before document hydration.
6. Intersect postings, with an explicitly labelled OR fallback when strict matching is empty.
7. Hydrate only the compact shards needed for the requested result window.

## Source policy

`config/search-policy.json` controls sources excluded from the public aggregated index and records the reason. Official Linux distributions, Academic Torrents, and Internet Archive open/public-domain collections remain the default indexed scope. Sources whose collection-level authorization cannot be established are disabled rather than silently mixed into the default index.

## Validation

```bash
python -m pytest -q
python scripts/build_index.py --config config/sources.yml --policy config/search-policy.json
python scripts/validate_v3_backend.py --public-dir public --minimum-items 30000 --max-bytes 73400320
```

The GitHub Pages workflow runs the same checks before deployment.
