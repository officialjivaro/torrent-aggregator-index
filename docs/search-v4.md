# Search v4 contract

Search v4 is a versioned static token index designed for Cloudflare R2.

Clients first fetch `data/status.json`, then `data/v4/current.json`, then the immutable release manifest named by `manifest_path`. If either v4 request fails, the website loads `data/v3/manifest.json`; v2 remains the last compatibility path.

The release manifest contains category, source, and scope lookup arrays; compact document-shard ranges; token-bucket part metadata; and the posting format. A token descriptor either contains inline `postings` or a list of posting `chunks`. Clients fetch all bucket parts matching the token prefix, resolve the descriptor, filter postings by category and allowed scope, and hydrate only the document shards needed for the requested result page.

The default scope is `open-authorized`. `third-party-unverified` is never included unless the website’s explicit opt-in is enabled. The preference is stored only in the browser under `jt-aggregate-third-party`.

Release paths are immutable and receive long cache lifetimes. `data/v4/current.json`, `data/status.json`, `data/manifest.json`, and the backend information page are uploaded with revalidation caching. The publisher uploads `current.json` last so a partial upload cannot advertise an incomplete release.
