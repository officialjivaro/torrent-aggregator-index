# Search v4 contract

Search v4 is a versioned static token index designed for Cloudflare R2.

Clients first fetch `data/status.json`, then `data/v4/current.json`, then the immutable release manifest named by `manifest_path`. Production output is v4-only; clients should show an unavailable state if the current v4 contract cannot be loaded rather than request retired v3/v2 files.

The release manifest contains category, source, and scope lookup arrays; compact document-shard ranges; token-bucket part metadata; and the posting format. A token descriptor either contains inline `postings` or a list of posting `chunks`. Clients fetch all bucket parts matching the token prefix, resolve the descriptor, filter postings by category and allowed scope, and hydrate only the document shards needed for the requested result page.

Both `open-authorized` and `third-party-unverified` are included for new visitors. Third-party results remain visibly labelled, and visitors can turn them off; that preference is stored only in the browser under `jt-aggregate-third-party`.

Release paths are immutable and receive long cache lifetimes. `data/v4/current.json`, `data/status.json`, `data/manifest.json`, and the backend information page are uploaded with revalidation caching. The publisher uploads `current.json` last so a partial upload cannot advertise an incomplete release.

The local build streams compact documents and places token postings in a disposable SQLite index before sharding them. This bounds Python memory use for multi-million-record catalogs. The disposable database is deleted after a successful or failed build and is never published.
