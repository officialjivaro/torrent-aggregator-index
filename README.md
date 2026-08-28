# JTorrent Torrent Aggregator Index

This repository collects torrent metadata into a persistent local catalog, builds a versioned static search index, saves a GitHub Release backup, and publishes the live copy to Cloudflare R2 at `https://index.jtorrent.net`.

Routine updates are manual and local. No GitHub Action runs on pushes or on a schedule. The repository workflow is retained as a manually triggered emergency GitHub Pages fallback only.

## Architecture

1. Structured source adapters collect records and resume from checkpoints in `state/catalog.sqlite`.
2. The catalog merges duplicate infohashes and preserves source mirrors, first-seen, and last-seen timestamps.
3. `config/suppressions.yml` removes exact infohashes or URLs before public output is generated.
4. Search v4 writes one immutable release under `data/v4/releases/<release-id>/`.
5. `data/v4/current.json` is the small mutable pointer to that release.
6. Search v3 and v2 remain published as compatibility fallbacks.
7. Backup ZIP assets are uploaded to a versioned GitHub Release.
8. R2 receives immutable files first, mutable compatibility files next, and `current.json` last.

Cloudflare R2 is the serving origin. GitHub Releases and the local catalog/backups are recovery copies; Cloudflare does not need to pull the index from GitHub.

## Search scopes

The policy in `config/search-policy.json` is fail closed:

- `open-authorized` is included by default.
- `third-party-unverified` requires an explicit website opt-in.
- The Pirate Bay APIBay collector and YTS Meta Search are assigned to the opt-in scope.
- Nyaa, IGG, LimeTorrent, and Skidrow remain disabled. LinuxTracker remains disabled because it produced no usable records.

The opt-in changes visibility, not an item’s underlying authorization. Users must verify source, rights, and file safety.

## One-time setup

Install Python 3.12 or newer and [GitHub CLI](https://cli.github.com/). From the repository root on Windows, GitHub CLI authentication is:

```powershell
gh auth login --hostname github.com --git-protocol https --web
gh auth status
gh auth setup-git
```

Create an R2 Standard bucket named `torrent-aggregator-index`, connect `index.jtorrent.net` as its custom domain, and create an R2 S3 API token with Object Read & Write access restricted to that bucket. Save the one-time Access Key ID and Secret Access Key from Windows without putting them in the repository or command history:

```powershell
.\save-r2-credentials.ps1 -AccountId "your-account-id" -IncludeApiToken
```

The helper prompts for both S3 keys and the optional administrative API token with masked input, then writes `../2_credentials/torrent-aggregator-index.env` with a Windows ACL limited to the current user, SYSTEM, and local administrators. Both updater scripts load the three S3 values automatically; the API token is retained for explicitly authorized Cloudflare administration. Set `JT_CREDENTIALS_FILE` or pass Windows `-CredentialFile` only when the credentials live elsewhere. Do not commit credentials.

The bucket-scoped Object Read & Write token intentionally cannot change bucket configuration. Configure the one-time CORS policy in the R2 dashboard for `GET` and `HEAD` from `https://www.jtorrent.net` and `https://jtorrent.net`. Routine publishers leave `r2_manage_cors: false`, preserving least privilege.

## Run an update

Windows, from the repository root:

```powershell
.\update-index.cmd
```

Raspberry Pi or another Linux system:

```bash
chmod +x update-index.sh
./update-index.sh
```

The first network collection can take hours. Checkpoints are committed to SQLite continuously by the collectors, so a later run resumes rather than starting the full source discovery again. The final catalog merge is transactional.

Useful Windows options:

```powershell
.\update-index.ps1 -Offline -SkipGitHubBackup -SkipR2
.\update-index.ps1 -SkipTests
```

`-Offline` is for local smoke testing; do not use it for a production release unless the persistent catalog already contains the intended records. Skipping tests is available for an operator who has already run the same local checks, but the default is safer.

## What the updater does

The root script performs these steps in order:

1. creates `.venv` if needed and installs missing pinned dependencies;
2. runs the Python tests and the fail-closed policy audit;
3. collects enabled sources and builds v4 plus the v3/v2 fallbacks;
4. validates the generated index and governance contract locally;
5. creates local backup bundles under `backups/<release-id>/`;
6. creates or updates `index-<release-id>` in GitHub Releases;
7. publishes to R2 and changes `data/v4/current.json` last.

If GitHub backup creation fails, R2 is not changed. If R2 publishing fails, the previous R2 pointer remains live and the new local/GitHub backups remain available.

## Capacity and retention

There are no arbitrary record-count caps on the two expanded collectors. Capacity is controlled by bytes and provider cost:

- local SQLite soft limit: 16 GiB;
- generated/R2 soft limit: 8 GiB;
- GitHub Release asset soft limit: 1.8 GiB per asset;
- R2 v4 retention: latest three immutable releases;
- local backup retention: latest two releases;
- GitHub Releases: not automatically deleted.

Crossing the SQLite limit rolls back the catalog merge. Crossing the R2 limit stops publication before the current pointer changes. Backup assets are grouped into multiple ZIP files when needed.

## Public endpoints

```text
/data/status.json
/data/source-health.json
/data/manifest.json
/data/v4/current.json
/data/v4/releases/<release-id>/manifest.json
/data/v4/releases/<release-id>/tokens/*.json
/data/v4/releases/<release-id>/postings/**/*.json
/data/v4/releases/<release-id>/docs/*.json
/data/v3/manifest.json
/data/v2/manifest.json
```

Each v4 posting is:

```text
[doc_id, category_index, source_index, scope_index, static_score]
```

## Source and rights operations

Configured sources live in `config/sources.yml`. Scope assignments and disabled-source reasons live in `config/search-policy.json`. Do not enable a source by deleting a policy entry alone; the source configuration, adapter, scope, and policy contract must agree.

For an accepted copyright or safety suppression, add the exact lowercase infohash or exact URL to `config/suppressions.yml`, run the local tests, then publish a new index release. Do not store claimant personal information in that file.

## Manual fallback workflow

`.github/workflows/update-index.yml` has only `workflow_dispatch`. It is not part of normal updates and consumes no Actions minutes unless started manually. It keeps the previous GitHub Pages deployment path as a recovery option.

## Local commands

```powershell
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe scripts\build_index.py --config config\sources.yml --policy config\search-policy.json --offline
.\.venv\Scripts\python.exe scripts\validate_v4_backend.py --public-dir public --minimum-items 1 --max-bytes 8589934592
```

Further details are in `docs/search-v4.md` and the adapter documentation under `docs/`.
