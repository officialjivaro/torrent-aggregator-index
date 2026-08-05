# Phase 4 post-deploy runtime checklist

Run this checklist after the Phase 4 commit passes GitHub Actions and Cloudflare Pages reports a successful production deployment.

## Deployment identity

1. Confirm the deployed Git commit is the Phase 4 commit on `main`.
2. Confirm the Pages project is `website-jtorrent-net`.
3. Confirm the build command is `npm run build`.
4. Confirm the output directory is `dist`.
5. Confirm Pages Functions are detected from `functions/`.
6. Open both the `pages.dev` production URL and `https://www.jtorrent.net/` and confirm they serve the same current build.

If the custom domain still shows a legacy homepage with only Home/Blog navigation or old Squarespace-style content, inspect the Pages custom-domain attachment and Cloudflare DNS for a conflicting legacy record. Do not change repository code to compensate for a domain that is still attached to the previous host.

## Homepage search product

1. Confirm the top navigation exposes Torrenting, Privacy, Proxies, VPN, Apps, Software, Providers, and Blog.
2. Confirm Best VPNs and Best Proxies remain above the search interface.
3. Confirm exactly two modes: Search Sites and Aggregated Results.
4. Select all Search Sites sources, search for `ubuntu iso`, and confirm 15 in-page destination links are generated.
5. Confirm no external destination receives the query until its generated link is opened.
6. Run `ubuntu iso` in Aggregated Results and confirm the UI loads a manifest, token buckets, and required document shards rather than downloading the entire dataset.
7. Confirm errors and empty results are explained without exposing raw stack traces.

## Editorial routes

1. Open `/blog/`, `/blog/page/2/`, and `/blog/page/3/` directly and after refresh.
2. Open one article from each hub directly and after refresh.
3. Confirm article TOC links move to the correct deterministic heading IDs.
4. Confirm responsive tables can be scrolled at 320px without page-level horizontal overflow.
5. Confirm light and dark themes persist across homepage, hubs, articles, Blog pages, providers, apps, and legal pages.
6. Confirm the mobile menu is keyboard and touch operable and returns focus predictably.
7. Confirm visible focus indicators and reduced-motion behavior.
8. Confirm `/404-test-route` returns the custom noindex 404 page.

## Functions and redirects

1. Request a verified legacy Blog path and confirm one direct 301 to its canonical article.
2. Confirm `/blog/` and numbered Blog pages are not intercepted by the legacy Function.
3. Confirm an unknown retired `/blog/<slug>` path redirects to `/blog/`.
4. Open one provider route under `/go/` and confirm a 302 response, no-store caching, and noindex robot headers.
5. Confirm an unknown `/go/` slug returns 404.
6. Confirm unsupported POST requests to `/go/*` and `/blog/*` return 405.

## Search backend boundary

1. Confirm `officialjivaro.github.io/backend-jtorrent/data/status.json` is reachable from the browser.
2. Confirm the frontend Content Security Policy allows that origin and no broader unneeded origin.
3. Confirm `data/status.json` reports search contract v3 and `data/v3/manifest.json` is reachable without copying backend shards into the frontend repository.
4. Confirm the deployed backend repository is `officialjivaro/backend-jtorrent`.

## Sign-off record

Record:

- Deployment commit.
- Cloudflare deployment URL.
- Custom-domain result.
- Browser and version.
- Desktop and mobile viewport checks.
- Search Sites result count.
- Aggregated Results query and result state.
- Legacy redirect tested.
- Affiliate redirect tested.
- Any defect and its exact route.
