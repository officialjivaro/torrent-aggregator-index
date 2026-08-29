#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from jtorrent_backend.search_v3 import tokenize  # noqa: E402


def read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def size_tree(root: Path) -> int:
    return sum(path.stat().st_size for path in root.rglob("*") if path.is_file())


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--public-dir", default="public")
    parser.add_argument("--max-bytes", type=int, default=8 * 1024 * 1024 * 1024)
    parser.add_argument("--minimum-items", type=int, default=1)
    args = parser.parse_args()
    public = Path(args.public_dir)
    errors: list[str] = []
    required = [
        public / "data/status.json",
        public / "data/source-health.json",
        public / "data/manifest.json",
        public / "data/v4/current.json",
    ]
    for path in required:
        if not path.is_file():
            errors.append(f"Missing required generated file: {path}")
    if errors:
        print("\n".join(errors), file=sys.stderr)
        return 1

    status, root, current = (read_json(required[index]) for index in [0, 2, 3])
    manifest_path = public / str(current.get("manifest_path") or "")
    if not manifest_path.is_file():
        errors.append(f"Current pointer references a missing v4 manifest: {manifest_path}")
        v4 = {}
    else:
        v4 = read_json(manifest_path)

    if status.get("search_contract") != "v4": errors.append("status.json does not advertise search contract v4")
    if root.get("search_contract") != "v4": errors.append("root manifest does not advertise v4")
    if current.get("version") != 4 or v4.get("version") != 4: errors.append("v4 pointer or manifest has the wrong version")
    if current.get("release_id") != v4.get("release_id"): errors.append("v4 pointer and manifest release IDs differ")
    if status.get("search_manifest") != "data/v4/current.json": errors.append("status does not point to v4 current.json")
    if status.get("fallback_manifest"): errors.append("status unexpectedly advertises a legacy fallback")
    if int(v4.get("item_count", 0)) < args.minimum_items: errors.append(f"v4 item count is below {args.minimum_items}")
    if int(v4.get("item_count", 0)) != int(status.get("item_count", -1)): errors.append("status and v4 item counts differ")
    if v4.get("posting_format") != ["doc_id", "category_index", "source_index", "scope_index", "static_score"]: errors.append("v4 posting format is invalid")
    if v4.get("default_scope") != "open-authorized": errors.append("v4 default scope is not open-authorized")
    if "third-party-unverified" not in (v4.get("scope_definitions") or {}): errors.append("v4 scope definitions omit third-party-unverified")

    entries = [*v4.get("doc_shards", []), *v4.get("token_buckets", [])]
    for entry in entries:
        path = public / str(entry.get("path") or "")
        if not path.is_file(): errors.append(f"Manifest references a missing file: {path}")
        elif path.stat().st_size != int(entry.get("bytes", -1)): errors.append(f"Manifest byte count is stale: {path}")
    for bucket in v4.get("token_buckets", []):
        bucket_path = public / str(bucket.get("path") or "")
        if not bucket_path.is_file():
            continue
        payload = read_json(bucket_path)
        for descriptor in (payload.get("tokens") or {}).values():
            for chunk in descriptor.get("chunks", []):
                path = public / str(chunk.get("path") or "")
                if not path.is_file(): errors.append(f"Posting descriptor references a missing chunk: {path}")
                elif path.stat().st_size != int(chunk.get("bytes", -1)): errors.append(f"Posting chunk byte count is stale: {path}")

    for query, expected in {"public domain": ["public", "domain"], "creative commons movie": ["creative", "commons", "movie"], "ubuntu iso": ["ubuntu", "iso"]}.items():
        if tokenize(query) != expected: errors.append(f"Tokenization regression for {query!r}: {tokenize(query)!r}")
    total_bytes = size_tree(public)
    if total_bytes > args.max_bytes: errors.append(f"Generated backend is {total_bytes:,} bytes; budget is {args.max_bytes:,}")
    index_html = (public / "index.html").read_text(encoding="utf-8") if (public / "index.html").is_file() else ""
    if "Search contract</strong><br>v4" not in index_html: errors.append("Backend status page does not describe the v4 contract")

    if errors:
        print("\n".join(f"ERROR: {message}" for message in errors), file=sys.stderr)
        return 1
    print(f"v4-only backend validated: {v4['item_count']:,} items, {len(v4.get('doc_shards', []))} document shards, {len(v4.get('token_buckets', []))} token-bucket parts, and {total_bytes:,} bytes.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
