from __future__ import annotations

import hashlib
import json
import re
import shutil
from collections import defaultdict
from pathlib import Path
from typing import Any

from .search_v3 import STOPWORDS, _as_int, _bucket, _read_json, _token_scores_for_doc, _write_json


DEFAULT_DOC_SHARD_BYTES = 256 * 1024
DEFAULT_POSTING_CHUNK_ENTRIES = 10_000
DEFAULT_BUCKET_SHARD_BYTES = 512 * 1024


def _release_id(generated_at: Any) -> str:
    compact = re.sub(r"[^0-9A-Za-z]+", "", str(generated_at or ""))
    return f"v4-{compact or 'unknown'}"


def _load_v3_docs(public_dir: Path) -> list[dict[str, Any]]:
    manifest = _read_json(public_dir / "data" / "v3" / "manifest.json")
    rows: list[dict[str, Any]] = []
    for shard in manifest.get("doc_shards", []):
        path = public_dir / str(shard.get("path") or "")
        payload = _read_json(path)
        if not isinstance(payload, list):
            raise RuntimeError(f"Expected a JSON array in {path}")
        rows.extend(payload)
    rows.sort(key=lambda row: int(row.get("doc_id", -1)))
    if [int(row.get("doc_id", -1)) for row in rows] != list(range(len(rows))):
        raise RuntimeError("v3 doc_id values are not a contiguous zero-based sequence.")
    return rows


def _write_doc_shards(
    public_dir: Path,
    release_relative: str,
    docs: list[dict[str, Any]],
    max_bytes: int,
) -> list[dict[str, Any]]:
    metadata: list[dict[str, Any]] = []
    current: list[dict[str, Any]] = []
    current_bytes = 2

    def flush() -> None:
        nonlocal current, current_bytes
        if not current:
            return
        number = len(metadata) + 1
        relative = f"{release_relative}/docs/docs-{number:05d}.json"
        path = public_dir / relative
        _write_json(path, current)
        metadata.append({
            "id": f"docs-{number:05d}",
            "path": relative,
            "count": len(current),
            "bytes": path.stat().st_size,
            "start_doc_id": int(current[0]["doc_id"]),
            "end_doc_id": int(current[-1]["doc_id"]),
        })
        current = []
        current_bytes = 2

    for doc in docs:
        encoded = json.dumps(doc, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        projected = current_bytes + len(encoded) + (1 if current else 0)
        if current and projected > max_bytes:
            flush()
        current.append(doc)
        current_bytes += len(encoded) + (1 if len(current) > 1 else 0)
    flush()
    return metadata


def _write_token_buckets(
    public_dir: Path,
    release_relative: str,
    postings: dict[str, list[list[int]]],
    *,
    prefix_chars: int,
    chunk_entries: int,
    bucket_shard_bytes: int,
) -> tuple[list[dict[str, Any]], int]:
    grouped: dict[str, list[tuple[str, list[list[int]]]]] = defaultdict(list)
    for token, values in sorted(postings.items()):
        values.sort(key=lambda entry: (-int(entry[4]), int(entry[0])))
        grouped[_bucket(token, prefix_chars)].append((token, values))

    metadata: list[dict[str, Any]] = []
    chunk_count = 0
    for bucket_id, token_rows in sorted(grouped.items()):
        descriptors: list[tuple[str, dict[str, Any]]] = []
        for token, values in token_rows:
            if len(values) <= chunk_entries:
                descriptors.append((token, {"count": len(values), "postings": values}))
                continue
            token_digest = hashlib.sha256(token.encode("utf-8")).hexdigest()[:16]
            chunks: list[dict[str, Any]] = []
            for offset in range(0, len(values), chunk_entries):
                number = offset // chunk_entries + 1
                relative = f"{release_relative}/postings/{bucket_id}/{token_digest}-{number:05d}.json"
                path = public_dir / relative
                rows = values[offset : offset + chunk_entries]
                _write_json(path, rows)
                chunks.append({"path": relative, "count": len(rows), "bytes": path.stat().st_size})
                chunk_count += 1
            descriptors.append((token, {"count": len(values), "chunks": chunks}))

        part = 1
        current: dict[str, dict[str, Any]] = {}

        def flush() -> None:
            nonlocal part, current
            if not current:
                return
            relative = f"{release_relative}/tokens/{bucket_id}-{part:03d}.json"
            path = public_dir / relative
            _write_json(path, {"bucket": bucket_id, "part": part, "tokens": current})
            metadata.append({
                "id": bucket_id,
                "part": part,
                "path": relative,
                "bytes": path.stat().st_size,
                "token_count": len(current),
            })
            part += 1
            current = {}

        for token, descriptor in descriptors:
            projected = dict(current)
            projected[token] = descriptor
            encoded_size = len(json.dumps(projected, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
            if current and encoded_size > bucket_shard_bytes:
                flush()
            current[token] = descriptor
        flush()
    return metadata, chunk_count


def _write_info_page(public_dir: Path, status: dict[str, Any]) -> None:
    generated = status.get("generated_at") or "unknown"
    html = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>JTorrent Search Index</title><meta name="robots" content="noindex,follow">
<style>body{{font-family:system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;max-width:920px;margin:2rem auto;padding:0 1rem;line-height:1.55;color:#17212b}}.grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(190px,1fr));gap:1rem}}.card{{border:1px solid #d7e0e7;border-radius:12px;padding:1rem;background:#f8fafc}}a{{color:#006f95}}</style></head>
<body><h1>JTorrent search index</h1><p>Versioned static search data used by <strong>www.jtorrent.net</strong>.</p>
<div class="grid"><div class="card"><strong>Search contract</strong><br>v4</div><div class="card"><strong>Indexed records</strong><br>{status.get('item_count', 0):,}</div><div class="card"><strong>Healthy sources</strong><br>{status.get('healthy_source_count', 0)}</div><div class="card"><strong>Generated</strong><br>{generated}</div></div>
<h2>Public endpoints</h2><ul><li><a href="data/status.json">status</a></li><li><a href="data/v4/current.json">current v4 release</a></li><li><a href="data/source-health.json">source health</a></li><li><a href="data/v3/manifest.json">v3 fallback</a></li></ul>
<p>The default client scope includes authorized and open sources. Third-party unverified results require an explicit user opt-in.</p></body></html>"""
    (public_dir / "index.html").write_text(html, encoding="utf-8")


def build_search_v4(public_dir: Path, output_options: dict[str, Any] | None = None) -> dict[str, Any]:
    public_dir = Path(public_dir)
    output_options = output_options or {}
    options = output_options.get("search_v4") if isinstance(output_options.get("search_v4"), dict) else {}
    if options.get("enabled", True) is False:
        return {"enabled": False}

    max_doc_bytes = max(64 * 1024, _as_int(options.get("doc_shard_bytes"), DEFAULT_DOC_SHARD_BYTES))
    prefix_chars = max(1, min(4, _as_int(options.get("token_bucket_prefix_chars"), 2)))
    max_query_tokens = max(2, _as_int(options.get("max_query_tokens"), 8))
    max_index_tokens = max(8, _as_int(options.get("max_tokens_per_record"), 32))
    chunk_entries = max(100, _as_int(options.get("posting_chunk_entries"), DEFAULT_POSTING_CHUNK_ENTRIES))
    bucket_shard_bytes = max(64 * 1024, _as_int(options.get("bucket_shard_bytes"), DEFAULT_BUCKET_SHARD_BYTES))

    root_path = public_dir / "data" / "manifest.json"
    root = _read_json(root_path)
    old_status = _read_json(public_dir / "data" / "status.json")
    docs = _load_v3_docs(public_dir)
    categories = sorted({str(doc.get("category")) for doc in docs if doc.get("category")})
    sources = sorted({str(doc.get("source_id")) for doc in docs if doc.get("source_id")})
    scopes = sorted({str(doc.get("policy_scope") or "open-authorized") for doc in docs})
    category_map = {value: index for index, value in enumerate(categories)}
    source_map = {value: index for index, value in enumerate(sources)}
    scope_map = {value: index for index, value in enumerate(scopes)}

    postings: dict[str, list[list[int]]] = defaultdict(list)
    for doc in docs:
        doc_id = int(doc["doc_id"])
        category_index = category_map.get(str(doc.get("category")), -1)
        source_index = source_map.get(str(doc.get("source_id")), -1)
        scope_index = scope_map.get(str(doc.get("policy_scope") or "open-authorized"), -1)
        for token, score in _token_scores_for_doc(doc, max_index_tokens).items():
            postings[token].append([doc_id, category_index, source_index, scope_index, score])

    release_id = _release_id(root.get("generated_at"))
    release_relative = f"data/v4/releases/{release_id}"
    release_root = public_dir / release_relative
    shutil.rmtree(release_root, ignore_errors=True)
    doc_shards = _write_doc_shards(public_dir, release_relative, docs, max_doc_bytes)
    token_buckets, posting_chunk_count = _write_token_buckets(
        public_dir,
        release_relative,
        postings,
        prefix_chars=prefix_chars,
        chunk_entries=chunk_entries,
        bucket_shard_bytes=bucket_shard_bytes,
    )

    manifest_path = f"{release_relative}/manifest.json"
    manifest = {
        "version": 4,
        "release_id": release_id,
        "type": "versioned-static-token-index",
        "generated_at": root.get("generated_at"),
        "item_count": len(docs),
        "doc_count": len(docs),
        "token_count": len(postings),
        "tokenization_version": 3,
        "token_bucket_prefix_chars": prefix_chars,
        "max_query_tokens": max_query_tokens,
        "max_tokens_per_record": max_index_tokens,
        "posting_chunk_entries": chunk_entries,
        "posting_chunk_count": posting_chunk_count,
        "categories": categories,
        "sources": sources,
        "scopes": scopes,
        "scope_definitions": root.get("policy_scopes", {}),
        "default_scope": root.get("default_index_scope", "open-authorized"),
        "doc_shards": doc_shards,
        "token_buckets": token_buckets,
        "stopwords": sorted(STOPWORDS),
        "posting_format": ["doc_id", "category_index", "source_index", "scope_index", "static_score"],
        "query_model": "Fetch the current release pointer, load only matching token-bucket parts and posting chunks, filter by category/source/scope indexes, then hydrate the required compact document shards.",
    }
    _write_json(public_dir / manifest_path, manifest, pretty=True)

    current = {
        "version": 4,
        "release_id": release_id,
        "generated_at": root.get("generated_at"),
        "item_count": len(docs),
        "manifest_path": manifest_path,
        "fallback_manifest": "data/v3/manifest.json",
    }
    _write_json(public_dir / "data" / "v4" / "current.json", current, pretty=True)

    status = dict(old_status)
    status.update({
        "search_contract": "v4",
        "search_manifest": "data/v4/current.json",
        "fallback_manifest": "data/v3/manifest.json",
        "release_id": release_id,
        "scope_definitions": root.get("policy_scopes", {}),
        "default_scope": root.get("default_index_scope", "open-authorized"),
        "counts_by_scope": root.get("counts_by_scope", {}),
    })
    _write_json(public_dir / "data" / "status.json", status, pretty=True)

    root["search_v4"] = {
        "version": 4,
        "release_id": release_id,
        "item_count": len(docs),
        "current_path": "data/v4/current.json",
        "manifest_path": manifest_path,
        "fallback_manifest": "data/v3/manifest.json",
    }
    root["search_contract"] = "v4"
    root["status_path"] = "data/status.json"
    _write_json(root_path, root, pretty=True)

    pointer = {
        "mode": "versioned-search-pointer",
        "message": "Use data/status.json and data/v4/current.json. v3 remains available as a fallback.",
        "status_path": "data/status.json",
        "search_v4_current_path": "data/v4/current.json",
        "search_v3_manifest_path": "data/v3/manifest.json",
        "total_results": len(docs),
    }
    for name in ["search-index.json", "search-index.min.json", "search-index.summary.json"]:
        _write_json(public_dir / "data" / name, pointer, pretty=True)
    _write_info_page(public_dir, status)
    return {"enabled": True, "status": status, "manifest": manifest, "current": current}
