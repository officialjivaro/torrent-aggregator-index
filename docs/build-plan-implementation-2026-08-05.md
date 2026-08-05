# JTorrent backend build-plan implementation — 2026-08-05

## Changes

- Added `config/search-policy.json` as the public-index source policy.
- Disabled repeatedly failing or collection-level uncertain optional sources from the default aggregated index while preserving the reasons for review.
- Added search contract v3 with meaningful domain-word tokenization.
- Added scored postings encoded as `[doc_id, category_index, source_index, static_score]`.
- Added category/source filtering before document hydration.
- Added compact approximately 64 KiB document shards.
- Added `data/status.json` and `data/source-health.json`.
- Retained a lightweight v2 compatibility token surface that reuses v3 document shards.
- Removed duplicate legacy full, compact, search, category, source, and v2 document corpora after generation.
- Added synthetic contract tests and a generated-artifact validator.
- Enforced a 70 MiB GitHub Pages artifact budget in CI.

## Real-artifact validation

The implementation was applied to the latest successful 39,739-record Pages artifact during development. The generated file tree was reduced from roughly 230 MiB to 48.9 MB of actual file bytes while keeping v2 compatibility. Sampled first-page compact-document transfer stayed below 1.5 MB for `public domain`, `creative commons`, `archive`, `movie`, `linux`, `Ubuntu ISO`, `Academic Torrents`, `film`, `audio`, `research data`, and `official torrent`.

The real scheduled build after source-policy application is expected to contain fewer records because uncertain/failed optional sources are disabled before collection. CI enforces the current minimum of 30,000 records; it will fail rather than deploy an unexpectedly thin artifact.

## Deployment order

1. Deploy this backend package and wait for the GitHub Pages workflow to pass.
2. Confirm `data/status.json` advertises `search_contract: v3`.
3. Deploy the frontend package. It prefers v3 and falls back to the compatibility v2 surface.
