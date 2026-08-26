# JTorrent backend final governance

Date: 2026-08-19
Repository: `officialjivaro/torrent-aggregator-index`

## Final production contract

The backend remains a Python-generated static search index deployed through GitHub Pages. Search contract v3 is primary; the compact v2 surface reuses v3 document shards during the compatibility period.

The public build now fails closed when `config/search-policy.json` is missing, invalid, or inconsistent with `config/sources.yml`. Sources with uncertain collection-level authorization must remain explicitly policy-disabled. Required official/open sources cannot be policy-disabled.

## Governance checks

The final audit validates:

- policy contract version and `open-official-research` scope;
- unique source IDs and supported adapters;
- required source metadata and minimums;
- HTTPS source and license URLs;
- explicit exclusion of uncertain-rights sources;
- exact policy-disabled source inventory;
- v3 output enabled and retired duplicate output disabled;
- at least 30,000 generated items;
- at least eight healthy sources and zero failed sources;
- v3/v2 manifest compatibility;
- generated file references and byte counts;
- no policy-disabled source in public source metadata;
- a 70 MiB uncompressed artifact budget.

## Commands

```powershell
python -m pytest -q
python scripts/audit_governance.py --config config/sources.yml --policy config/search-policy.json
python scripts/build_index.py --config config/sources.yml --policy config/search-policy.json
python scripts/validate_v3_backend.py --public-dir public --minimum-items 30000 --max-bytes 73400320
python scripts/verify_final_phase.py --generated-dir public
```

The scheduled workflow runs the same policy and generated-artifact checks before deploying GitHub Pages and uploads a 30-day governance evidence artifact.
