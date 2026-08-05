# Phase 5 — Final package and migration closeout

## Status

The Jivaro Torrent editorial-system migration is finalized in Mode C: the existing dependency-free Node generator remains the only editorial generator and Cloudflare Pages remains the only deployment architecture.

## Canonical editorial ownership

The complete source model is:

```text
content/sections.json
content/<section>/<slug>/article.json
content/<section>/<slug>/body.html
```

There is no competing `content/articles.json` monolith, remote article store, framework content layer, database, CMS, Markdown parser, or second static generator.

The seven canonical editorial sections are:

- Torrenting
- Privacy
- Proxies
- VPN
- Apps
- Software
- Providers

The Blog route is a generated cross-section archive and legacy namespace; it is not a canonical source section.

## Final generated system

One source item now drives all applicable public output:

- Complete static article HTML.
- Section and Blog cards.
- Newest-first deterministic ordering.
- Crawlable pagination.
- Canonical, previous, and next links.
- Article, Breadcrumb, FAQ, and ItemList schema.
- Deterministic H2/H3 IDs and article TOCs.
- Visible dates, reading time, hero, sources, author, disclosure, and related content.
- Sitemap and sitemap index.
- Global and per-section RSS.
- Editorial search index.
- Content and legacy-redirect manifests.
- Direct historical Blog redirects.
- Deliberate empty-hub behavior.
- Draft and noindex exclusion.

The homepage search product, the 15 Search Sites sources, the v2 Aggregated Results backend, provider directory, affiliate allowlist, Apps relationships, Functions, legal pages, and shared design system remain outside editorial-source duplication.

## Final baseline

The migration closeout baseline records:

```text
27 canonical published articles
10 historical launch-authority articles
7 populated editorial sections
43 public routes
44 HTML files including the custom 404
3 Blog archive pages
11 direct historical redirects
56 responsive article images
68,386 editorial words
129 deterministic output files
```

The release baseline is stored in `docs/editorial-migration/phase-5-release.json`. The exact output hash is a closeout record, not a permanent prohibition on future articles. Standard QC enforces minimum migration invariants; `npm run verify:phase5:release` additionally locks the current closeout output.

## Final release gate

`npm run qc` now performs:

1. Canonical source validation.
2. Editorial loader/scaffolder behavior tests.
3. Tree-integrity tests.
4. Live-verifier contract tests against a local mock deployment.
5. First production build and complete generated-output audit.
6. Source-mutation comparison.
7. Second production build and complete generated-output audit.
8. Byte-for-byte deterministic output comparison.
9. Phase 4 deployment-safety verification.
10. Phase 5 final-acceptance verification.

GitHub Actions runs the same command from a clean Linux checkout on pull requests and pushes to `main`.

## Runtime verification

The repository includes `npm run verify:live`, which performs no-cache HTTP checks against:

- `https://www.jtorrent.net`
- `https://jtorrent.net`
- `https://website-jtorrent-net.pages.dev`
- `https://officialjivaro.github.io/backend-jtorrent`

It validates the search-first homepage, rejects retired Squarespace-style markers, checks a canonical article and hub, verifies Blog pagination, feeds, sitemap, robots, editorial manifests, affiliate redirects, historical redirects, apex behavior, Pages project output, and the v3 aggregated-search status/manifest with its v2 compatibility surface.

The live command is intentionally separate from deterministic CI because external DNS, Cloudflare propagation, and backend availability are runtime conditions rather than source determinism.

## Final package policy

The Phase 5 ZIP is a complete root-ready source package. It excludes:

- `.git`
- `node_modules`
- `dist`
- `.astro`
- `.wrangler`
- OS metadata
- screenshots and test reports
- nested archives
- secrets

It includes the canonical sources, generator, Functions, responsive assets, design system, QC scripts, workflow, publishing script, authoring documentation, and migration records required to reconstruct the current production build.

## Publishing

With the ZIP in the repository root and the Phase 4 publisher already present, run:

```powershell
.\Publish-Content.ps1 `
  -ZipName "jtorrent-editorial-migration-phase-5-final.zip" `
  -CommitMessage "Finalize Phase 5 editorial publishing system"
```

The publisher validates the ZIP before extraction, deletes it immediately after extraction, runs deterministic QC before staging, stages only changed package-owned paths, commits, rebases, reruns QC, confirms a clean source tree, and pushes only after success.

## Post-deployment closeout

After Cloudflare reports a successful deployment, run:

```powershell
npm run verify:live
```

Then manually confirm keyboard operation, focus states, mobile navigation, both search modes, theme switching, and representative article/table layouts on the production domain. Static and mocked runtime checks must not be represented as a substitute for this final browser pass.
