from __future__ import annotations

import json
import os
import shutil
import sqlite3
import tempfile
from collections import Counter
from pathlib import Path
from typing import Any, Iterable

from .catalog import CatalogStore, Suppressions
from .models import TorrentItem
from .search_v3 import STOPWORDS, _bucket, _compact_doc, _source_health, _token_scores_for_doc, _write_json
from .search_integrity import required_scope
from .search_v4 import _release_id
from .fsutil import remove_tree


DEFAULT_DOC_SHARD_BYTES = 256 * 1024
DEFAULT_POSTING_CHUNK_ENTRIES = 10_000
DEFAULT_BUCKET_SHARD_BYTES = 512 * 1024


def _as_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _write_doc_shard(
    public_dir: Path,
    release_relative: str,
    number: int,
    docs: list[dict[str, Any]],
) -> dict[str, Any]:
    relative = f"{release_relative}/docs/docs-{number:05d}.json"
    path = public_dir / relative
    _write_json(path, docs)
    return {
        "id": f"docs-{number:05d}",
        "path": relative,
        "count": len(docs),
        "bytes": path.stat().st_size,
        "start_doc_id": int(docs[0]["doc_id"]),
        "end_doc_id": int(docs[-1]["doc_id"]),
    }


def _write_posting_chunk(
    public_dir: Path,
    release_relative: str,
    bucket_id: str,
    token_number: int,
    chunk_number: int,
    rows: list[list[int]],
) -> dict[str, Any]:
    relative = (
        f"{release_relative}/postings/{bucket_id}/"
        f"token-{token_number:07d}-{chunk_number:05d}.json"
    )
    path = public_dir / relative
    _write_json(path, rows)
    return {"path": relative, "count": len(rows), "bytes": path.stat().st_size}


def _write_bucket_part(
    public_dir: Path,
    release_relative: str,
    bucket_id: str,
    part: int,
    tokens: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    relative = f"{release_relative}/tokens/{bucket_id}-{part:03d}.json"
    path = public_dir / relative
    _write_json(path, {"bucket": bucket_id, "part": part, "tokens": tokens})
    return {
        "id": bucket_id,
        "part": part,
        "path": relative,
        "bytes": path.stat().st_size,
        "token_count": len(tokens),
    }


def _public_pages(public_dir: Path, status: dict[str, Any], base_url: str, custom_domain: str | None) -> None:
    generated = status.get("generated_at") or "unknown"
    html = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>JTorrent Search Index</title><meta name="robots" content="noindex,follow">
<style>body{{font-family:system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;max-width:920px;margin:2rem auto;padding:0 1rem;line-height:1.55;color:#17212b}}.grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(190px,1fr));gap:1rem}}.card{{border:1px solid #d7e0e7;border-radius:12px;padding:1rem;background:#f8fafc}}a{{color:#006f95}}</style></head>
<body><h1>JTorrent search index</h1><p>Versioned static search data used by <strong>www.jtorrent.net</strong>.</p>
<div class="grid"><div class="card"><strong>Search contract</strong><br>v4</div><div class="card"><strong>Indexed records</strong><br>{status.get('item_count', 0):,}</div><div class="card"><strong>Healthy sources</strong><br>{status.get('healthy_source_count', 0)}</div><div class="card"><strong>Generated</strong><br>{generated}</div></div>
<h2>Public endpoints</h2><ul><li><a href="data/status.json">status</a></li><li><a href="data/v4/current.json">current v4 release</a></li><li><a href="data/source-health.json">source health</a></li><li><a href="data/manifest.json">root manifest</a></li></ul>
<p>Authorized and open results are enabled by default. Third-party unverified results remain visible by default and can be disabled by the user.</p></body></html>"""
    (public_dir / "index.html").write_text(html, encoding="utf-8")
    (public_dir / "robots.txt").write_text("User-agent: *\nAllow: /\n", encoding="utf-8")
    if custom_domain:
        (public_dir / "CNAME").write_text(custom_domain.strip() + "\n", encoding="utf-8")
    urls = [base_url.rstrip("/")] if base_url else []
    if base_url:
        urls.extend([
            f"{base_url.rstrip('/')}/data/manifest.json",
            f"{base_url.rstrip('/')}/data/v4/current.json",
            f"{base_url.rstrip('/')}/data/source-health.json",
        ])
    sitemap = '<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
    sitemap += "".join(f"  <url><loc>{url}</loc></url>\n" for url in urls)
    sitemap += "</urlset>\n"
    (public_dir / "sitemap.xml").write_text(sitemap, encoding="utf-8")


def _first_pass(
    store: CatalogStore,
    source_ids: set[str],
    suppressions: Suppressions,
) -> tuple[int, Counter[str], Counter[str], Counter[str]]:
    item_count = 0
    categories: Counter[str] = Counter()
    sources: Counter[str] = Counter()
    scopes: Counter[str] = Counter()
    for item in store.iter_active(source_ids, suppressions):
        item_count += 1
        categories[item.category or "uncategorized"] += 1
        sources[item.source_id or "unknown"] += 1
        scopes[required_scope(item.policy_scope)] += 1
    return item_count, categories, sources, scopes


def _insert_postings(
    connection: sqlite3.Connection,
    rows: list[tuple[str, str, int, int, int, int, int]],
) -> None:
    if not rows:
        return
    connection.executemany(
        "INSERT INTO postings (bucket, token, doc_id, category_index, source_index, scope_index, score) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        rows,
    )
    rows.clear()


def _build_documents_and_postings(
    store: CatalogStore,
    source_ids: set[str],
    suppressions: Suppressions,
    public_dir: Path,
    release_relative: str,
    posting_db: Path,
    *,
    category_map: dict[str, int],
    source_map: dict[str, int],
    scope_map: dict[str, int],
    doc_shard_bytes: int,
    max_index_tokens: int,
    prefix_chars: int,
) -> list[dict[str, Any]]:
    connection = sqlite3.connect(posting_db)
    connection.execute("PRAGMA journal_mode=OFF")
    connection.execute("PRAGMA synchronous=OFF")
    connection.execute("PRAGMA temp_store=FILE")
    connection.execute("PRAGMA cache_size=-131072")
    connection.execute(
        "CREATE TABLE postings (bucket TEXT NOT NULL, token TEXT NOT NULL, doc_id INTEGER NOT NULL, "
        "category_index INTEGER NOT NULL, source_index INTEGER NOT NULL, scope_index INTEGER NOT NULL, "
        "score INTEGER NOT NULL)"
    )
    doc_metadata: list[dict[str, Any]] = []
    docs: list[dict[str, Any]] = []
    docs_bytes = 2
    posting_rows: list[tuple[str, str, int, int, int, int, int]] = []

    def flush_docs() -> None:
        nonlocal docs, docs_bytes
        if docs:
            doc_metadata.append(_write_doc_shard(public_dir, release_relative, len(doc_metadata) + 1, docs))
            docs = []
            docs_bytes = 2

    try:
        for doc_id, item in enumerate(store.iter_active(source_ids, suppressions)):
            row = item.to_dict()
            row["doc_id"] = doc_id
            doc = _compact_doc(row)
            encoded = json.dumps(doc, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
            projected = docs_bytes + len(encoded) + (1 if docs else 0)
            if docs and projected > doc_shard_bytes:
                flush_docs()
            docs.append(doc)
            docs_bytes += len(encoded) + (1 if len(docs) > 1 else 0)

            category_index = category_map.get(str(item.category or "uncategorized"), -1)
            source_index = source_map.get(str(item.source_id or "unknown"), -1)
            scope_index = scope_map[required_scope(item.policy_scope)]
            for token, score in _token_scores_for_doc(doc, max_index_tokens).items():
                posting_rows.append((
                    _bucket(token, prefix_chars), token, doc_id,
                    category_index, source_index, scope_index, score,
                ))
            if len(posting_rows) >= 20_000:
                _insert_postings(connection, posting_rows)
                connection.commit()
            if doc_id and doc_id % 100_000 == 0:
                print(f"Streamed {doc_id:,} documents into v4 staging", flush=True)
        flush_docs()
        _insert_postings(connection, posting_rows)
        connection.commit()
        connection.execute(
            "CREATE INDEX postings_order_idx ON postings (bucket, token, score DESC, doc_id)"
        )
        connection.commit()
    finally:
        connection.close()
    return doc_metadata


def _build_token_files(
    posting_db: Path,
    public_dir: Path,
    release_relative: str,
    *,
    chunk_entries: int,
    bucket_shard_bytes: int,
) -> tuple[list[dict[str, Any]], int, int]:
    connection = sqlite3.connect(posting_db)
    cursor = connection.execute(
        "SELECT bucket, token, doc_id, category_index, source_index, scope_index, score "
        "FROM postings ORDER BY bucket, token, score DESC, doc_id"
    )
    metadata: list[dict[str, Any]] = []
    token_count = posting_chunk_count = token_number = 0
    active_bucket: str | None = None
    bucket_part = 1
    bucket_tokens: dict[str, dict[str, Any]] = {}
    bucket_bytes = 32
    active_token: str | None = None
    token_rows: list[list[int]] = []
    token_chunks: list[dict[str, Any]] = []
    token_total = 0

    def flush_bucket() -> None:
        nonlocal bucket_tokens, bucket_bytes, bucket_part
        if active_bucket is not None and bucket_tokens:
            metadata.append(_write_bucket_part(
                public_dir, release_relative, active_bucket, bucket_part, bucket_tokens
            ))
            bucket_part += 1
            bucket_tokens = {}
            bucket_bytes = 32

    def add_descriptor(token: str, descriptor: dict[str, Any]) -> None:
        nonlocal bucket_bytes
        encoded_bytes = len(json.dumps({token: descriptor}, ensure_ascii=False, separators=(",", ":")).encode("utf-8")) + 1
        if bucket_tokens and bucket_bytes + encoded_bytes > bucket_shard_bytes:
            flush_bucket()
        bucket_tokens[token] = descriptor
        bucket_bytes += encoded_bytes

    def flush_posting_rows() -> None:
        nonlocal token_rows, posting_chunk_count
        if token_rows:
            token_chunks.append(_write_posting_chunk(
                public_dir, release_relative, str(active_bucket), token_number,
                len(token_chunks) + 1, token_rows,
            ))
            posting_chunk_count += 1
            token_rows = []

    def finish_token() -> None:
        nonlocal token_count, token_rows, token_chunks, token_total
        if active_token is None:
            return
        if token_chunks:
            flush_posting_rows()
            descriptor = {"count": token_total, "chunks": token_chunks}
        else:
            descriptor = {"count": token_total, "postings": token_rows}
        add_descriptor(active_token, descriptor)
        token_count += 1
        token_rows = []
        token_chunks = []
        token_total = 0

    try:
        for bucket_id, token, doc_id, category_id, source_id, scope_id, score in cursor:
            bucket_id = str(bucket_id)
            token = str(token)
            if active_bucket is None:
                active_bucket = bucket_id
            if bucket_id != active_bucket:
                finish_token()
                flush_bucket()
                active_bucket = bucket_id
                active_token = None
                bucket_part = 1
            if token != active_token:
                finish_token()
                active_token = token
                token_number += 1
            token_rows.append([int(doc_id), int(category_id), int(source_id), int(scope_id), int(score)])
            token_total += 1
            if len(token_rows) > chunk_entries:
                # The first overflow proves this token needs chunk files. Keep the
                # already buffered rows and write exact-size chunks from here on.
                rows_to_write = token_rows[:chunk_entries]
                token_rows = token_rows[chunk_entries:]
                token_chunks.append(_write_posting_chunk(
                    public_dir, release_relative, bucket_id, token_number,
                    len(token_chunks) + 1, rows_to_write,
                ))
                posting_chunk_count += 1
        finish_token()
        flush_bucket()
    finally:
        cursor.close()
        connection.close()
    return metadata, token_count, posting_chunk_count


def build_search_v4_stream(
    store: CatalogStore,
    source_ids: set[str],
    suppressions: Suppressions,
    source_rows: list[dict[str, Any]],
    root_manifest: dict[str, Any],
    output_options: dict[str, Any],
    *,
    minimum_items: int = 0,
    enforce_minimum: bool = True,
) -> dict[str, Any]:
    """Build the v4 contract with bounded RAM and a disposable disk posting index."""

    options = output_options.get("search_v4") if isinstance(output_options.get("search_v4"), dict) else {}
    doc_shard_bytes = max(64 * 1024, _as_int(options.get("doc_shard_bytes"), DEFAULT_DOC_SHARD_BYTES))
    prefix_chars = max(1, min(4, _as_int(options.get("token_bucket_prefix_chars"), 2)))
    max_query_tokens = max(2, _as_int(options.get("max_query_tokens"), 8))
    max_index_tokens = max(8, _as_int(options.get("max_tokens_per_record"), 32))
    chunk_entries = max(100, _as_int(options.get("posting_chunk_entries"), DEFAULT_POSTING_CHUNK_ENTRIES))
    bucket_shard_bytes = max(64 * 1024, _as_int(options.get("bucket_shard_bytes"), DEFAULT_BUCKET_SHARD_BYTES))
    public_dir = Path(root_manifest.pop("_output_dir"))

    item_count, category_counts, source_counts, scope_counts = _first_pass(store, source_ids, suppressions)
    if enforce_minimum and minimum_items and item_count < minimum_items:
        root_manifest.setdefault("errors", []).append(
            f"total item_count={item_count} is below min_total_items={minimum_items}"
        )
    for source in source_rows:
        if source.get("id"):
            source["indexed_item_count"] = int(source_counts.get(str(source["id"]), 0))

    root_manifest.update({
        "item_count": item_count,
        "counts_by_category": dict(sorted(category_counts.items())),
        "counts_by_source": dict(sorted(source_counts.items())),
        "counts_by_scope": dict(sorted(scope_counts.items())),
        "search_contract": "v4",
        "v4_only": True,
        "historical_coverage": store.historical_coverage(),
    })
    categories = sorted(category_counts)
    sources = sorted(source_counts)
    scopes = sorted(scope_counts)
    category_map = {value: index for index, value in enumerate(categories)}
    source_map = {value: index for index, value in enumerate(sources)}
    scope_map = {value: index for index, value in enumerate(scopes)}
    release_id = _release_id(root_manifest.get("generated_at"))
    release_relative = f"data/v4/releases/{release_id}"

    free_before = shutil.disk_usage(public_dir.parent).free
    configured_work_limit = _as_int(options.get("working_space_soft_limit_bytes"), 64 * 1024 * 1024 * 1024)
    estimated_working = max(1024 * 1024 * 1024, store.size_bytes() * 8)
    required_working = min(estimated_working, configured_work_limit)
    if free_before < required_working:
        raise RuntimeError(
            f"Streaming build estimates {required_working:,} bytes of working space but only "
            f"{free_before:,} bytes are free."
        )

    if public_dir.exists():
        remove_tree(public_dir)
    (public_dir / "data").mkdir(parents=True, exist_ok=True)
    (public_dir / ".nojekyll").write_text("", encoding="utf-8")
    release_root = public_dir / release_relative
    release_root.mkdir(parents=True, exist_ok=True)

    fd, posting_name = tempfile.mkstemp(prefix="jtorrent-v4-postings-", suffix=".sqlite", dir=public_dir.parent)
    os.close(fd)
    posting_db = Path(posting_name)
    try:
        doc_shards = _build_documents_and_postings(
            store, source_ids, suppressions, public_dir, release_relative, posting_db,
            category_map=category_map, source_map=source_map, scope_map=scope_map,
            doc_shard_bytes=doc_shard_bytes, max_index_tokens=max_index_tokens,
            prefix_chars=prefix_chars,
        )
        token_buckets, token_count, posting_chunk_count = _build_token_files(
            posting_db, public_dir, release_relative,
            chunk_entries=chunk_entries, bucket_shard_bytes=bucket_shard_bytes,
        )
    finally:
        posting_db.unlink(missing_ok=True)

    manifest_path = f"{release_relative}/manifest.json"
    manifest = {
        "version": 4,
        "release_id": release_id,
        "type": "versioned-static-token-index",
        "generated_at": root_manifest.get("generated_at"),
        "item_count": item_count,
        "doc_count": item_count,
        "token_count": token_count,
        "tokenization_version": 3,
        "token_bucket_prefix_chars": prefix_chars,
        "max_query_tokens": max_query_tokens,
        "max_tokens_per_record": max_index_tokens,
        "posting_chunk_entries": chunk_entries,
        "posting_chunk_count": posting_chunk_count,
        "categories": categories,
        "sources": sources,
        "scopes": scopes,
        "scope_definitions": root_manifest.get("policy_scopes", {}),
        "default_scope": root_manifest.get("default_index_scope", "open-authorized"),
        "counts_by_scope": root_manifest.get("counts_by_scope", {}),
        "historical_coverage": root_manifest.get("historical_coverage", {}),
        "doc_shards": doc_shards,
        "token_buckets": token_buckets,
        "stopwords": sorted(STOPWORDS),
        "posting_format": ["doc_id", "category_index", "source_index", "scope_index", "static_score"],
        "query_model": "Fetch current.json, load matching token-bucket parts and posting chunks, filter indexes, then hydrate only required document shards.",
    }
    _write_json(public_dir / manifest_path, manifest, pretty=True)
    current = {
        "version": 4,
        "release_id": release_id,
        "generated_at": root_manifest.get("generated_at"),
        "item_count": item_count,
        "manifest_path": manifest_path,
    }
    _write_json(public_dir / "data/v4/current.json", current, pretty=True)

    health = _source_health(source_rows)
    healthy = sum(1 for item in health if item["state"] == "healthy")
    degraded = sum(1 for item in health if item["state"] == "degraded")
    failed = sum(1 for item in health if item["state"] == "failed")
    status = {
        "version": 1,
        "generated_at": root_manifest.get("generated_at"),
        "item_count": item_count,
        "enabled_source_count": root_manifest.get("enabled_source_count", len(health)),
        "healthy_source_count": healthy,
        "degraded_source_count": degraded,
        "failed_source_count": failed,
        "search_contract": "v4",
        "search_manifest": "data/v4/current.json",
        "release_id": release_id,
        "scope_definitions": root_manifest.get("policy_scopes", {}),
        "default_scope": root_manifest.get("default_index_scope", "open-authorized"),
        "counts_by_scope": root_manifest.get("counts_by_scope", {}),
        "historical_coverage": root_manifest.get("historical_coverage", {}),
    }
    _write_json(public_dir / "data/status.json", status, pretty=True)
    _write_json(public_dir / "data/source-health.json", {
        "generated_at": root_manifest.get("generated_at"),
        "sources": health,
        "historical_coverage": root_manifest.get("historical_coverage", {}),
    }, pretty=True)
    _write_json(public_dir / "data/sources.json", source_rows, pretty=True)

    root_manifest["search_v4"] = {
        "version": 4,
        "release_id": release_id,
        "item_count": item_count,
        "current_path": "data/v4/current.json",
        "manifest_path": manifest_path,
    }
    root_manifest["status_path"] = "data/status.json"
    root_manifest["capacity"] = {
        "catalog_bytes": store.size_bytes(),
        "free_bytes_before_build": free_before,
        "estimated_working_bytes": estimated_working,
        "working_space_soft_limit_bytes": configured_work_limit,
    }
    _write_json(public_dir / "data/manifest.json", root_manifest, pretty=True)
    pointer = {
        "mode": "versioned-search-pointer",
        "message": "Use data/status.json and data/v4/current.json.",
        "status_path": "data/status.json",
        "search_v4_current_path": "data/v4/current.json",
        "total_results": item_count,
    }
    for name in ["search-index.json", "search-index.min.json", "search-index.summary.json"]:
        _write_json(public_dir / "data" / name, pointer, pretty=True)
    _public_pages(
        public_dir, status, str(root_manifest.get("base_url") or ""),
        str(root_manifest.get("custom_domain") or "") or None,
    )
    return root_manifest
