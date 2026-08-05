#!/usr/bin/env python3
"""Validate the generated JTorrent v3/v2-compatibility search artifact."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from jtorrent_backend.search_v3 import tokenize  # noqa: E402


def read_json(path: Path):
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def size_tree(root: Path) -> int:
    return sum(path.stat().st_size for path in root.rglob("*") if path.is_file())


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--public-dir", default="public")
    parser.add_argument("--max-bytes", type=int, default=70 * 1024 * 1024)
    parser.add_argument("--minimum-items", type=int, default=1)
    args = parser.parse_args()
    public = Path(args.public_dir)
    errors: list[str] = []

    required = [
        public / "data/status.json",
        public / "data/source-health.json",
        public / "data/manifest.json",
        public / "data/v3/manifest.json",
        public / "data/v2/manifest.json",
    ]
    for path in required:
        if not path.is_file(): errors.append(f"Missing required generated file: {path}")
    if errors:
        print("\n".join(errors), file=sys.stderr)
        return 1

    status = read_json(required[0])
    root_manifest = read_json(required[2])
    v3 = read_json(required[3])
    v2 = read_json(required[4])
    health = read_json(public / "data/source-health.json")

    if status.get("search_contract") != "v3": errors.append("status.json does not advertise search contract v3")
    if not isinstance(health.get("sources"), list): errors.append("source-health.json is missing its source list")
    if not isinstance(health.get("disabled_sources", []), list): errors.append("source-health.json has an invalid disabled source list")
    if int(status.get("disabled_source_count", len(health.get("disabled_sources", [])))) != len(health.get("disabled_sources", [])): errors.append("status and source-health disabled-source counts differ")
    if v3.get("version") != 3: errors.append("v3 manifest has the wrong version")
    if v2.get("version") != 2 or v2.get("type") != "v3-compatibility-token-index": errors.append("v2 compatibility manifest is missing")
    if root_manifest.get("search_contract") != "v3": errors.append("root manifest does not advertise v3")
    if int(v3.get("item_count", 0)) < args.minimum_items: errors.append(f"v3 item count is below {args.minimum_items}")
    if int(v3.get("item_count", 0)) != int(status.get("item_count", -1)): errors.append("status and v3 item counts differ")
    if v2.get("doc_shards") != v3.get("doc_shards"): errors.append("v2 compatibility does not reuse compact v3 document shards")
    if any(not str(item.get("path", "")).startswith("data/v3/docs/") for item in v3.get("doc_shards", [])): errors.append("v3 document shard path is invalid")

    for entry in [*v3.get("doc_shards", []), *v3.get("token_buckets", []), *v2.get("token_buckets", [])]:
        path = public / str(entry.get("path", ""))
        if not path.is_file(): errors.append(f"Manifest references a missing file: {path}")
        elif path.stat().st_size != int(entry.get("bytes", -1)): errors.append(f"Manifest byte count is stale: {path}")
    if v3.get("posting_format") != ["doc_id", "category_index", "source_index", "static_score"]: errors.append("v3 posting format is invalid")

    for query, expected in {
        "public domain": ["public", "domain"],
        "creative commons movie": ["creative", "commons", "movie"],
        "internet archive": ["internet", "archive"],
        "ubuntu iso": ["ubuntu", "iso"],
        "academic torrents": ["academic", "torrents"],
    }.items():
        if tokenize(query) != expected: errors.append(f"Tokenization regression for {query!r}: {tokenize(query)!r}")

    total_bytes = size_tree(public)
    if total_bytes > args.max_bytes: errors.append(f"Generated backend is {total_bytes:,} bytes; budget is {args.max_bytes:,}")
    for retired in ["data/shards/full", "data/shards/compact", "data/search", "data/by-category", "data/by-source", "data/v2/docs"]:
        if (public / retired).exists(): errors.append(f"Retired duplicate output remains: {retired}")

    index_html = (public / "index.html").read_text(encoding="utf-8") if (public / "index.html").is_file() else ""
    if "Search contract</strong><br>v3" not in index_html or "data/status.json" not in index_html: errors.append("Backend status page does not describe the v3 contract")
    helper = (public / "jtorrent-search-v2.js").read_text(encoding="utf-8") if (public / "jtorrent-search-v2.js").is_file() else ""
    stopword_match = __import__("re").search(r"const STOPWORDS = new Set\((\[[^;]*?\])\);", helper)
    if stopword_match:
        helper_stopwords = set(json.loads(stopword_match.group(1)))
        if {"public", "domain", "creative", "commons", "archive", "official", "torrent", "download", "metadata"} & helper_stopwords:
            errors.append("v2 compatibility helper still removes meaningful domain terms")

    if errors:
        print("\n".join(f"ERROR: {message}" for message in errors), file=sys.stderr)
        return 1
    print(
        f"v3 backend validated: {v3['item_count']:,} items, "
        f"{len(v3.get('doc_shards', []))} compact document shards, "
        f"{total_bytes:,} total bytes, and v2 compatibility."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
