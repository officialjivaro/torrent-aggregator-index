#!/usr/bin/env bash
set -euo pipefail
cd -- "$(dirname -- "$0")"

if [[ ! -x .venv/bin/python ]]; then
  python3 -m venv .venv
fi
python=.venv/bin/python
if ! "$python" -c 'import requests, yaml' 2>/dev/null; then
  "$python" -m pip install -r requirements.txt
fi
if ! tar --version 2>&1 | grep -Eiq 'bsdtar|libarchive'; then
  printf 'A libarchive-compatible bsdtar is required. On Raspberry Pi OS: sudo apt install libarchive-tools\n' >&2
  exit 1
fi
"$python" scripts/import_history.py "$@"
printf 'Historical import complete. Run ./update-index.sh to refresh backups and publish the v4 index.\n'
