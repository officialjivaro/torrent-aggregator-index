# Full-site optimization — 2026-08-05

## Scope

This release applies the post-migration full-site QC plan without changing canonical editorial routes, original publication dates, the two-mode homepage product, the provider allowlist, or the Cloudflare Pages deployment architecture.

## Frontend changes

- Added a canonical Search Sites registry in `content/search-sources.json`.
- Made official/open/archive/research sources the default selection; retained prior third-party destinations as collapsed opt-in sources.
- Added persistent source selection and URL-restorable query, mode, category, and source state.
- Split shared, homepage-search, and provider-directory JavaScript into fingerprinted route-specific bundles.
- Added v3 Aggregated Results support with v2 fallback, category filtering before hydration, scored stable pagination, labelled AND-to-OR fallback, and incremental loading.
- Replaced current NetPeek product references with Aerod IP Lookup while preserving real historical legacy URLs.
- Updated Instanciar destinations to the canonical Jivaro software route.
- Standardized published article identity as Jivaro Torrent Editorial while retaining Harry Negron as founder on the About page.
- Refactored the deterministic hero-image generator to discover canonical per-item article packages and avoid rewriting unrelated artwork.
- Replaced the stale monolithic content-package prompt with the canonical article-folder workflow.
- Updated the safe publisher to consume and delete temporary package manifests before QC.
- Added a deterministic build fingerprint at `/.well-known/jtorrent-build.json` and extended live verification to compare production and Pages deployments.
- Removed implementation-facing footer/About copy and generated route-specific sitemap modification dates.

## Search-backend contract

The companion `officialjivaro/backend-jtorrent` package publishes search contract v3:

- compact status and source-health endpoints;
- meaningful domain-term tokenization;
- scored postings with category/source indexes;
- compact approximately 64 KiB result-document shards;
- stable filtered totals and first-page hydration;
- a lightweight v2 compatibility surface reusing v3 documents;
- removal of duplicate legacy full/compact/search corpora;
- policy-disabled uncertain or repeatedly failing optional sources;
- a 70 MiB Pages-artifact budget.

The backend package must be deployed first. The frontend prefers v3 and safely falls back to v2 until v3 is live.

## Deliberately deferred editorial work

No article was padded merely to hit an arbitrary word count. OSRS pages remain published pending traffic/backlink evidence; their disposition should be handled as an editorial/SEO decision, not a blind technical deletion. FAQ and related-content counts are no longer treated as ranking features, but changing them requires article-specific review rather than a bulk mechanical rewrite.

## Validation

The frontend release gate performs source validation, canonical image discovery, behavior tests, two deterministic builds, complete generated-output audits, immutable-source comparison, migration verification, and live-verifier contract tests. The backend workflow runs Python tests, builds the index, validates v3/v2 compatibility, checks domain-query tokenization, rejects duplicate legacy output, and enforces the artifact-size budget.
