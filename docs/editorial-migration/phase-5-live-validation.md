# Phase 5 live deployment validation

Run this after Cloudflare Pages has deployed the Phase 5 commit:

```powershell
npm run verify:live
```

The default command checks:

- Production custom domain: `https://www.jtorrent.net`
- Apex redirect: `https://jtorrent.net`
- Cloudflare Pages project URL: `https://website-jtorrent-net.pages.dev`
- Aggregated-search backend: `https://officialjivaro.github.io/backend-jtorrent`

## What the automated live verifier checks

- The homepage serves the current `Jivaro Torrent Search` build.
- The homepage contains exactly Search Sites and Aggregated Results.
- Best VPNs and Best Proxies remain above the search.
- All content hubs remain in navigation.
- No retired Squarespace/Command Palette markers are present.
- Security headers are attached to the production homepage.
- A canonical migrated article has one H1, a TOC, Article schema, and the correct canonical.
- The Torrenting hub includes ItemList schema and the migrated article.
- Blog page 2 has its own canonical plus previous/next links.
- RSS contains at least the migration baseline article count.
- Sitemap, sitemap index, robots, search index, and editorial content manifest are current.
- `/go/brightdata` returns a no-store/noindex 302 to the allowlisted HTTPS destination.
- A known historical Blog path returns a direct 301 to its canonical article.
- An unknown retired Blog path returns a direct 301 to `/blog/`.
- The Pages project URL serves the same current search build.
- The apex domain redirects to the `www` production domain.
- The aggregated backend exposes search contract v3, a valid v3 manifest, meaningful domain terms, and the retained v2 compatibility surface.

## Optional overrides

```powershell
npm run verify:live -- --timeout-ms 30000
npm run verify:live -- --skip-preview
npm run verify:live -- --skip-apex
npm run verify:live -- --skip-backend
npm run verify:live -- --skip-functions
npm run verify:live -- --base-url "https://preview.example.com" --skip-apex --skip-preview
```

Skipping a check means it remains unverified; it is not a passing result.

## Manual browser checks still required

1. Tab through the header, search modes, filters, results, and footer.
2. Confirm focus indicators are visible in both themes.
3. Test the mobile menu at 320px and 390px.
4. Switch Search Sites to Aggregated Results and run a lawful test query.
5. Confirm a generated Search Sites link receives the query only after it is opened.
6. Force a backend failure and confirm the Aggregated Results error state is understandable.
7. Switch light/dark themes and refresh to verify persistence.
8. Open an article with a table, FAQ, and related cards on mobile.
9. Refresh deep article, hub-pagination, and Blog-pagination URLs directly.
10. Confirm Cloudflare’s deployment corresponds to the pushed Git commit.
