# Frontend integration notes

The backend publishes static JSON through GitHub Pages. The frontend must not trigger GitHub Actions or download the whole corpus for each search.

## Preferred v3 flow

1. Fetch `https://officialjivaro.github.io/backend-jtorrent/data/status.json`.
2. When `search_contract` is `v3`, fetch `data/v3/manifest.json`.
3. Tokenize with the manifest's `stopwords`, `max_query_tokens`, and `token_bucket_prefix_chars`.
4. Fetch only the token buckets required by the query.
5. Each posting is `[doc_id, category_index, source_index, static_score]`; filter category or source before hydration.
6. Intersect token postings. When strict AND matching returns nothing, an OR fallback may be shown only when clearly labelled.
7. Use `doc_shards` to hydrate the requested result window from `data/v3/docs/*.json`.
8. Start with 20–25 results and use incremental pagination or “Load more.”

This keeps the status request small, avoids loading every document shard, and returns accurate category-filtered totals before documents are fetched.

## Compatibility

During the frontend transition, `data/v2/manifest.json` and `data/v2/tokens/*.json` remain available. They reuse the compact v3 document shards instead of duplicating the old v2 document corpus. New code should prefer v3.

## Useful endpoints

```text
/data/status.json
/data/source-health.json
/data/manifest.json
/data/v3/manifest.json
/data/v3/tokens/<prefix>.json
/data/v3/docs/*.json
/data/v2/manifest.json
/data/v2/tokens/<prefix>.json
```

## Result display

- Prefer the source/details page as the primary link.
- Show source, category, date, size, seeder/peer values, and copyright-status metadata only when present.
- Clearly distinguish public-domain, Creative Commons, open-source, and open-data records from unknown-rights records.
- A listing is not proof that the user has permission to download or redistribute the payload.
- Never describe the search index as anonymous, risk-free, or a legal determination.
