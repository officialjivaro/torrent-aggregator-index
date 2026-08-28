#!/usr/bin/env bash
set -euo pipefail
cd -- "$(dirname -- "$0")"

credentials_file="${JT_CREDENTIALS_FILE:-$(dirname -- "$PWD")/2_credentials/torrent-aggregator-index.env}"
if [[ -f "$credentials_file" ]]; then
  while IFS='=' read -r key value || [[ -n "$key" ]]; do
    key="${key%$'\r'}"
    value="${value%$'\r'}"
    case "$key" in
      CLOUDFLARE_ACCOUNT_ID|R2_ACCESS_KEY_ID|R2_SECRET_ACCESS_KEY)
        if [[ -z "${!key:-}" ]]; then
          export "$key=$value"
        fi
        ;;
    esac
  done < "$credentials_file"
fi
missing_credentials=()
for key in CLOUDFLARE_ACCOUNT_ID R2_ACCESS_KEY_ID R2_SECRET_ACCESS_KEY; do
  if [[ -z "${!key:-}" ]]; then
    missing_credentials+=("$key")
  fi
done
if ((${#missing_credentials[@]})); then
  printf 'Missing R2 credentials: %s. Create %s or set JT_CREDENTIALS_FILE.\n' "${missing_credentials[*]}" "$credentials_file" >&2
  exit 1
fi

if [[ ! -x .venv/bin/python ]]; then
  python3 -m venv .venv
fi
python=.venv/bin/python
if ! "$python" -c 'import boto3, bs4, bencodepy, feedparser, requests, yaml' 2>/dev/null; then
  "$python" -m pip install -r requirements.txt
fi

"$python" -m pytest -q
"$python" scripts/audit_governance.py --config config/sources.yml --policy config/search-policy.json --output artifacts/backend-governance-static.json
"$python" scripts/build_index.py --config config/sources.yml --policy config/search-policy.json
"$python" scripts/validate_v4_backend.py --public-dir public --minimum-items 30000 --max-bytes 8589934592
"$python" scripts/verify_final_phase.py --generated-dir public --output artifacts/backend-final-verification.json
"$python" scripts/publish_release.py prepare --public-dir public --backup-dir backups --manifest staging/release.json

tag=$("$python" -c 'import json; print(json.load(open("staging/release.json", encoding="utf-8"))["tag"])')
release_id=$("$python" -c 'import json; print(json.load(open("staging/release.json", encoding="utf-8"))["release_id"])')
item_count=$("$python" -c 'import json; print(json.load(open("staging/release.json", encoding="utf-8"))["item_count"])')
mapfile -t assets < <("$python" -c 'import json; print("\n".join(item["path"] for item in json.load(open("staging/release.json", encoding="utf-8"))["assets"]))')

gh auth status
if gh release view "$tag" --repo officialjivaro/torrent-aggregator-index >/dev/null 2>&1; then
  gh release upload "$tag" "${assets[@]}" --repo officialjivaro/torrent-aggregator-index --clobber
else
  gh release create "$tag" "${assets[@]}" --repo officialjivaro/torrent-aggregator-index --title "JTorrent index $release_id" --notes "Static JTorrent index backup with $item_count records. R2 remains the live serving origin."
fi
"$python" scripts/publish_release.py r2 --public-dir public
printf 'Index update complete: %s with %s records.\n' "$release_id" "$item_count"
