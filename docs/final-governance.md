# JTorrent backend final governance

Date: 2026-08-19
Repository: `officialjivaro/torrent-aggregator-index`

## Final production contract

The backend is a Python-generated static search index published to Cloudflare R2, with a local copy and a GitHub Release backup. Search contract v4 is primary; v3 remains the frontend fallback and the compact v2 surface reuses v3 document shards during the compatibility period.

The public build now fails closed when `config/search-policy.json` is missing, invalid, or inconsistent with `config/sources.yml`. Sources with uncertain collection-level authorization must remain explicitly policy-disabled. Required official/open sources cannot be policy-disabled.

## Governance checks

The final audit validates:

- policy contract version and `open-authorized` default scope;
- unique source IDs and supported adapters;
- required source metadata and minimums;
- HTTPS source and license URLs;
- explicit exclusion of uncertain-rights sources;
- exact policy-disabled source inventory;
- v3 output enabled and retired duplicate output disabled;
- at least 30,000 generated items;
- at least eight healthy sources, zero failed required sources, and at most two failed optional sources;
- v4/v3/v2 manifest compatibility;
- generated file references and byte counts;
- no policy-disabled source in public source metadata;
- an 8 GiB uncompressed artifact budget.

## Commands

```powershell
python -m pytest -q
python scripts/audit_governance.py --config config/sources.yml --policy config/search-policy.json
python scripts/build_index.py --config config/sources.yml --policy config/search-policy.json
python scripts/validate_v4_backend.py --public-dir public --minimum-items 30000 --max-bytes 8589934592
python scripts/verify_final_phase.py --generated-dir public
```

The manual local updater runs these checks before creating the local/GitHub backups and publishing R2. The GitHub workflow is a manually dispatched fallback and uploads governance evidence when used.
