# Jivaro Torrent

Jivaro Torrent is a dependency-free static publishing and search site built for Cloudflare Pages. The repository contains canonical editorial source, responsive artwork, a custom Node.js generator, and Pages Functions for legacy redirects and validated affiliate links.

## Local commands

```powershell
npm ci
npm run check
npm run build
npm run qc
npm run qc:single
npm run verify:phase3
npm run verify:phase4
npm run verify:phase5
npm run verify:phase5:release
npm run verify:live
npm run check:external
npm run new:content -- --section "privacy" --slug "example-slug" --title "Example Title"
```

`npm run qc` is the release gate. It validates canonical source, runs the editorial behavior tests and live-verifier contract tests, builds and audits the production site twice, compares the two output trees byte-for-byte, proves that generation did not mutate repository source, and verifies the final Phase 5 acceptance contract. `npm run qc:single` is available only as a faster diagnostic pass. Production output is written to `dist/`.

Cloudflare Pages should use:

- Build command: `npm run build`
- Output directory: `dist`
- Node.js: 20 or newer

## Search architecture

The homepage has two browser-based modes:

- **Search Sites** generates result-page links for the selected external sites. A site receives the query only after the user opens its generated link.
- **Aggregated Results** searches the scalable static token index published by `officialjivaro/backend-jtorrent` at `https://officialjivaro.github.io/backend-jtorrent`. The browser reads the compact backend status document, prefers the v3 scored/token-filtered index, and hydrates only compact document shards for the requested result window. A lightweight v2 compatibility surface remains available during rollout.

The compatibility endpoint at `/api/search` remains available for limited archive, academic, Linux, and broad-web queries, but it is not the homepage’s primary search path.

## Editorial architecture

The canonical section registry is:

```text
content/sections.json
```

Every editorial article is owned by exactly one source folder:

```text
content/<section>/<slug>/
├── article.json
└── body.html
```

The former `content/articles.json` monolith has been retired. New content must be created with `npm run new:content`; hubs, Blog archives, pagination, metadata, schema, sitemap, feeds, search data, related cards, and legacy redirects are generated from the per-item sources.

The generator:

- Excludes drafts from public output, hubs, sitemap, RSS, search, and related modules.
- Derives canonical routes from the section and slug.
- Validates body-only HTML and rejects document wrappers, H1, scripts, forms, unsafe URLs, inline events, and manual heading IDs.
- Assigns deterministic H2/H3 IDs and generates the article TOC.
- Generates crawlable Blog and hub pagination.
- Generates one global RSS feed plus per-hub feeds for populated feed-enabled sections.
- Generates `dist/editorial/content-manifest.json` and `dist/editorial/legacy-redirects.json` from canonical metadata.
- Preserves the shared article shell, metadata, schema, hero, FAQ, sources, author box, disclosure, and related cards.
- Keeps complete article bodies in generated static HTML.

See `docs/editorial-authoring.md` for the source model and publishing workflow. Phase records are under `docs/editorial-migration/`.

## Content and design files

- `content/site.json` controls the Jivaro Torrent Editorial identity, founder profile, provider listings, and Jivaro software relationships.
- `content/search-sources.json` is the canonical Search Sites registry; official/open sources are selected by default and third-party indexes are opt-in.
- `content/sections.json` is the canonical editorial-hub registry.
- `content/<section>/<slug>/` contains canonical article metadata and body fragments.
- `scripts/lib/content-loader.mjs` recursively loads, validates, sorts, and resolves canonical editorial source.
- `scripts/lib/body-html.mjs` validates and processes body-only HTML.
- `scripts/build.mjs` generates HTML, pagination, metadata, schema, feeds, sitemap, robots, manifests, redirects data, and the editorial search index.
- `scripts/qc.mjs` performs the single-build production audit.
- `scripts/run-qc.mjs` owns deterministic double-build QC and source-mutation detection.
- `scripts/lib/tree-integrity.mjs` creates stable source and output snapshots.
- `scripts/new-content.mjs` creates safe unpublished canonical drafts.
- `scripts/verify-editorial-phase3.mjs` proves all 27 migrated articles retain their routes, dates, metadata, body meaning, sources, related links, and word counts.
- `scripts/verify-editorial-phase4.mjs` verifies the deterministic runner, CI workflow, publisher, and deployment contract.
- `scripts/verify-editorial-phase5.mjs` verifies final migration acceptance and can lock the exact closeout output.
- `scripts/verify-live-deployment.mjs` performs post-deployment HTTP checks against the custom domain, apex, Pages URL, Functions, feeds, manifests, and aggregated-search backend.
- `scripts/test-live-deployment-verifier.mjs` proves the live verifier accepts the current contract and rejects the retired Squarespace-style homepage.
- `.github/workflows/editorial-qc.yml` runs the same release gate from a clean Linux checkout.
- `.github/workflows/external-link-qc.yml` checks the canonical Aerod IP Lookup and Jivaro Instanciar destinations weekly without making deterministic local QC depend on the network.
- `Publish-Content.ps1` safely extracts, validates, stages, rebases, revalidates, and pushes root-ready packages.
- `public/css/site.css` contains the shared responsive design system.
- `public/js/theme.js` applies the saved light/dark preference before rendering.
- `public/js/site.js` provides shared navigation and theme-adjacent core behavior.
- `public/js/search.js` is loaded only on the homepage and owns Search Sites, URL state, source selection, and v3/v2 Aggregated Results.
- `public/js/providers.js` is loaded only on the provider directory and owns provider filters.
- `public/assets/images/` contains the shipped WebP artwork.
- `scripts/generate-images.py` discovers canonical article packages and can generate one slug, missing artwork, or a dry-run inventory without touching unrelated images.
- `docs/jtorrent-content-package-prompt.txt` is the current canonical article-package prompt; it must not be replaced with the retired article monolith workflow.

## Safe package publishing

Place a root-ready ZIP in the repository root and run:

```powershell
.\Publish-Content.ps1 `
  -ZipName "jtorrent-content-package.zip" `
  -CommitMessage "Add privacy guide: ipv6-leaks-with-a-vpn"
```

The publisher assumes PowerShell is already open in the repository root. It validates the archive before extraction, deletes the ZIP immediately after extraction, runs deterministic QC before staging and after rebasing, stages only package-owned paths, and stops before pushing on any error. It never uses destructive Git cleanup commands.

GitHub Actions runs the same deterministic QC on pull requests and pushes to `main`. The final migration record is `docs/editorial-migration/phase-5-release.json`; implementation and runtime guidance are in `phase-5-finalization.md` and `phase-5-live-validation.md`.

## Phase 5 closeout

The five-phase migration is complete. The optimized release contains 27 canonical articles, seven editorial sections, 43 public routes, three Blog pages, eleven direct historical redirects, 56 responsive article images, a generated deployment fingerprint, and split page-specific JavaScript. The baseline output hash is recorded for closeout, while normal QC continues to allow future articles.

After Cloudflare deploys a production commit, run:

```powershell
npm run verify:live
```

This runtime check is separate from deterministic CI because DNS, cache propagation, the Pages project URL, the apex redirect, Functions, and the external v3 backend (with v2 fallback) are live-service conditions. Browser visual and keyboard checks remain a manual post-deployment requirement.

## Cloudflare Pages Functions

- `/go/*` validates an affiliate alias against a fixed HTTPS destination allowlist before issuing a temporary redirect.
- `/blog/*` preserves the generated Blog index and pagination while reading specific historical redirects from `dist/editorial/legacy-redirects.json`.
- `/api/search` is retained as a compatibility search endpoint.
