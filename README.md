# JTorrent search backend

This repository builds the scheduled static search index used by `https://www.jtorrent.net` and publishes it through GitHub Pages at `https://officialjivaro.github.io/backend-jtorrent`.

The GitHub Actions workflow runs when backend files change, can be started manually, and runs twice per day at 12:30 AM and 12:30 PM Japan time. It fetches the enabled sources from `config/sources.yml`, applies the public-index policy in `config/search-policy.json`, normalizes and deduplicates records, builds the versioned search contract, validates the result, and deploys the `public/` artifact.

## Current search architecture

Search contract **v3** is the preferred frontend interface. It keeps meaningful domain words such as `public`, `domain`, `creative`, `commons`, `archive`, `official`, `torrent`, `download`, and `metadata` searchable.

The v3 flow is:

1. Fetch `data/status.json`.
2. Confirm `search_contract` is `v3`.
3. Fetch `data/v3/manifest.json`.
4. Tokenize the query using the manifest's stopword and token settings.
5. Fetch only the matching token buckets under `data/v3/tokens/`.
6. Filter postings by category or source before document hydration.
7. Intersect strict matches, or explicitly label an OR fallback when no strict match exists.
8. Hydrate only the requested result window from compact shards under `data/v3/docs/`.

Each v3 posting is:

```text
[doc_id, category_index, source_index, static_score]
```

A compact v2 compatibility surface remains available during the frontend transition. It reuses the v3 document shards instead of duplicating a second document corpus.

## Published endpoints

```text
/data/status.json                     Small build/search status record
/data/source-health.json              Source counts, errors, and policy-disabled sources
/data/manifest.json                   Root build manifest and version pointers
/data/v3/manifest.json                Preferred search-contract manifest
/data/v3/tokens/*.json                Scored, filterable token postings
/data/v3/docs/*.json                  Compact result-document shards
/data/v2/manifest.json                Compatibility manifest
/data/v2/tokens/*.json                Compatibility doc-id postings
/data/search-index*.json              Small version pointers, not duplicate corpora
/data/sources.json                    Source metadata
```

The build no longer publishes duplicate legacy full, compact, search, category, source, and v2 document corpora. CI enforces a 70 MiB uncompressed artifact budget.

## Source policy

`config/sources.yml` remains the complete configured source registry. `config/search-policy.json` controls which configured sources are included in the default public aggregated index.

The default index prioritizes official, open-source, public-domain, Creative Commons, and research sources. Optional sources that repeatedly fail or whose collection-level authorization cannot be established are disabled by policy and recorded with a reason in `data/source-health.json`.

A source with `required: true` can still fail the scheduled build when it produces an error or falls below its configured minimum. Optional source failures are reported without automatically blocking healthy required sources.

## Local setup

```bash
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
python -m pip install -r requirements.txt
python -m pytest -q
```

Run a full network build:

```bash
python scripts/build_index.py \
  --config config/sources.yml \
  --policy config/search-policy.json

python scripts/validate_v3_backend.py \
  --public-dir public \
  --minimum-items 30000 \
  --max-bytes 73400320
```

Run a no-network smoke build:

```bash
python scripts/build_index.py \
  --config config/sources.yml \
  --policy config/search-policy.json \
  --offline

python scripts/validate_v3_backend.py \
  --public-dir public \
  --minimum-items 1 \
  --max-bytes 73400320
```

`public/` is generated output and is ignored by Git.

## GitHub Pages deployment

1. Keep **Settings → Pages → Source** set to **GitHub Actions**.
2. Push backend changes to `main`, or run **Update JTorrent Backend Index** manually.
3. Wait for both the build and deploy jobs to pass.
4. Verify `https://officialjivaro.github.io/backend-jtorrent/data/status.json` reports `"search_contract": "v3"` before deploying a frontend that prefers v3.

The frontend remains backward compatible with the v2 compatibility surface during rollout.

## Adding or re-enabling a source

Supported adapter types are:

```text
direct_list
html_torrent_links
rss_feed
internet_archive_advancedsearch
```

Add the source in `config/sources.yml`. When a source is intentionally excluded from the default public index, add a precise reason to `config/search-policy.json`. Remove that policy entry only after the source has been revalidated and produces useful records.

Large sources may define source-level `max_items`, `rows`, and `max_pages`. Source-level limits override the global default.

## Deduplication

Deduplication priority is:

1. infohash from torrent metadata or magnet URI;
2. normalized magnet infohash;
3. normalized `.torrent` URL;
4. normalized source/details URL;
5. normalized title plus size.

Duplicate records merge useful metadata and mirrors rather than discarding the richer record.

## Further documentation

- `docs/search-v3.md` — v3 contract and performance behavior.
- `docs/frontend-integration.md` — frontend implementation sequence.
- `docs/architecture-for-millions.md` — current static limits and future scaling path.
- `docs/github-actions-guide.md` — scheduled build and failure handling.
- `docs/source-adapters.md` — source adapter configuration.
