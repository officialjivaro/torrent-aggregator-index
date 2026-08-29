from __future__ import annotations

import json
import re
import shutil
import unicodedata
from collections import defaultdict
from pathlib import Path
from typing import Any

DEFAULT_DOC_SHARD_BYTES = 256 * 1024
DEFAULT_PREFIX_CHARS = 2
DEFAULT_MAX_QUERY_TOKENS = 8
DEFAULT_MAX_INDEX_TOKENS = 32
DEFAULT_MAX_POSTINGS = 50_000

# Only grammatical/high-frequency words belong here. Domain terms such as
# public, domain, creative, commons, archive, official, torrent, download,
# and metadata must remain searchable.
STOPWORDS = {
    "a", "an", "and", "are", "as", "at", "be", "been", "but", "by",
    "can", "do", "does", "for", "from", "had", "has", "have", "if", "in",
    "into", "is", "it", "its", "not", "of", "on", "or", "our", "that",
    "the", "their", "this", "to", "was", "were", "will", "with", "you", "your",
}
TOKEN_RE = re.compile(r"[a-z0-9][a-z0-9.+_]{1,63}")


def _write_json(path: Path, value: Any, *, pretty: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2 if pretty else None, sort_keys=pretty, separators=None if pretty else (",", ":"))
        handle.write("\n")


def _read_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def _as_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _normalize(value: Any) -> str:
    text = unicodedata.normalize("NFKD", str(value or "")).encode("ascii", "ignore").decode("ascii").lower()
    return re.sub(r"[^a-z0-9.+_]+", " ", text)


def tokenize(value: Any, *, maximum: int = DEFAULT_MAX_QUERY_TOKENS) -> list[str]:
    output: list[str] = []
    seen: set[str] = set()
    for match in TOKEN_RE.finditer(_normalize(value)):
        token = match.group(0).strip("._+")
        if len(token) < 2 or token in STOPWORDS:
            continue
        if token.isdigit() and len(token) < 4:
            continue
        if token not in seen:
            seen.add(token)
            output.append(token)
        if len(output) >= maximum:
            break
    return output


def _token_scores_for_doc(doc: dict[str, Any], maximum: int) -> dict[str, int]:
    fields = {
        "title": _normalize(doc.get("title")),
        "category": _normalize(doc.get("category")),
        "source": _normalize(doc.get("source_name")),
        "source_id": _normalize(doc.get("source_id")),
        "tags": _normalize(" ".join(doc.get("tags") or [])),
        "description": _normalize(doc.get("description")),
    }
    tokens = tokenize(" ".join(fields.values()), maximum=maximum)
    title_words = set(fields["title"].split())
    source_words = set(fields["source"].split()) | set(fields["source_id"].split())
    tag_words = set(fields["tags"].split())
    category_words = set(fields["category"].split())
    scores: dict[str, int] = {}
    for token in tokens:
        score = 0
        if token in title_words:
            score += 16
        elif token in fields["title"]:
            score += 10
        if token in source_words:
            score += 5
        elif token in fields["source"] or token in fields["source_id"]:
            score += 3
        if token in tag_words:
            score += 4
        if token in category_words or token in fields["category"]:
            score += 3
        if token in fields["description"]:
            score += 1
        scores[token] = max(1, min(score, 31))
    return scores


def _compact_doc(row: dict[str, Any]) -> dict[str, Any]:
    description = str(row.get("description") or "").strip()
    if len(description) > 180:
        description = description[:177].rstrip() + "..."
    keys = [
        "doc_id", "title", "source_id", "source_name", "source_url", "details_url",
        "torrent_url", "magnet", "category", "size", "seeders", "leechers",
        "date_added", "copyright_status", "policy_scope", "mirrors", "first_seen", "last_seen", "tags",
    ]
    compact = {key: row.get(key) for key in keys if row.get(key) not in [None, "", [], {}]}
    if description:
        compact["description"] = description
    return compact


def _load_v2_docs(public_dir: Path) -> list[dict[str, Any]]:
    docs_dir = public_dir / "data" / "v2" / "docs"
    files = sorted(docs_dir.glob("*.json"))
    if not files:
        raise RuntimeError("No v2 document shards were found; build the existing backend before generating v3.")
    rows: list[dict[str, Any]] = []
    for path in files:
        payload = _read_json(path)
        if not isinstance(payload, list):
            raise RuntimeError(f"Expected a JSON array in {path}")
        rows.extend(payload)
    rows.sort(key=lambda row: int(row.get("doc_id", 0)))
    if [int(row.get("doc_id", -1)) for row in rows] != list(range(len(rows))):
        raise RuntimeError("v2 doc_id values are not a contiguous zero-based sequence.")
    return rows


def _write_doc_shards(public_dir: Path, docs: list[dict[str, Any]], max_bytes: int) -> list[dict[str, Any]]:
    target = public_dir / "data" / "v3" / "docs"
    shutil.rmtree(target, ignore_errors=True)
    target.mkdir(parents=True, exist_ok=True)
    metadata: list[dict[str, Any]] = []
    current: list[dict[str, Any]] = []
    current_bytes = 2

    def flush() -> None:
        nonlocal current, current_bytes
        if not current:
            return
        number = len(metadata) + 1
        relative = f"data/v3/docs/docs-{number:04d}.json"
        path = public_dir / relative
        _write_json(path, current)
        metadata.append({
            "id": f"docs-{number:04d}",
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


def _bucket(token: str, prefix_chars: int) -> str:
    cleaned = re.sub(r"[^a-z0-9]", "", token)
    return (cleaned[:prefix_chars] or "__").ljust(prefix_chars, "_")


def _write_token_buckets(
    public_dir: Path,
    postings: dict[str, list[list[int]]],
    *,
    prefix_chars: int,
    max_postings: int,
) -> tuple[list[dict[str, Any]], list[str]]:
    target = public_dir / "data" / "v3" / "tokens"
    shutil.rmtree(target, ignore_errors=True)
    target.mkdir(parents=True, exist_ok=True)
    buckets: dict[str, dict[str, list[list[int]]]] = defaultdict(dict)
    truncated: list[str] = []
    for token, values in sorted(postings.items()):
        values = sorted(values, key=lambda entry: (-int(entry[3]), int(entry[0])))
        if max_postings and len(values) > max_postings:
            values = values[:max_postings]
            truncated.append(token)
        buckets[_bucket(token, prefix_chars)][token] = values
    metadata: list[dict[str, Any]] = []
    for bucket_id, token_map in sorted(buckets.items()):
        relative = f"data/v3/tokens/{bucket_id}.json"
        path = public_dir / relative
        payload = {"bucket": bucket_id, "token_count": len(token_map), "tokens": token_map}
        _write_json(path, payload)
        metadata.append({"id": bucket_id, "path": relative, "bytes": path.stat().st_size, "token_count": len(token_map)})
    return metadata, truncated



def _write_v2_compatibility(
    public_dir: Path,
    postings: dict[str, list[list[int]]],
    *,
    prefix_chars: int,
    max_postings: int,
    doc_shards: list[dict[str, Any]],
    categories: list[str],
    sources: list[str],
    generated_at: str | None,
) -> dict[str, Any]:
    """Publish a compact v2-compatible surface while v3 rolls out.

    The currently deployed frontend requests data/v2/tokens/<bucket>.json
    directly and hydrates documents using the paths in v2.doc_shards.  Keep
    that contract without duplicating the large v2 document corpus: v2 points
    at the compact v3 document shards and stores doc-id-only postings.
    """

    v2_root = public_dir / "data" / "v2"
    shutil.rmtree(v2_root / "docs", ignore_errors=True)
    shutil.rmtree(v2_root / "tokens", ignore_errors=True)
    (v2_root / "tokens").mkdir(parents=True, exist_ok=True)

    buckets: dict[str, dict[str, list[int]]] = defaultdict(dict)
    truncated: list[str] = []
    for token, values in sorted(postings.items()):
        ranked = sorted(values, key=lambda entry: (-int(entry[3]), int(entry[0])))
        doc_ids = [int(entry[0]) for entry in ranked]
        if max_postings and len(doc_ids) > max_postings:
            doc_ids = doc_ids[:max_postings]
            truncated.append(token)
        buckets[_bucket(token, prefix_chars)][token] = doc_ids

    token_metadata: list[dict[str, Any]] = []
    for bucket_id, token_map in sorted(buckets.items()):
        relative = f"data/v2/tokens/{bucket_id}.json"
        path = public_dir / relative
        _write_json(path, {"bucket": bucket_id, "token_count": len(token_map), "tokens": token_map})
        token_metadata.append({
            "id": bucket_id,
            "path": relative,
            "bytes": path.stat().st_size,
            "token_count": len(token_map),
        })

    manifest = {
        "version": 2,
        "type": "v3-compatibility-token-index",
        "generated_at": generated_at,
        "item_count": sum(int(shard.get("count", 0)) for shard in doc_shards),
        "doc_count": sum(int(shard.get("count", 0)) for shard in doc_shards),
        "token_count": len(postings),
        "token_bucket_prefix_chars": prefix_chars,
        "max_postings_per_token": max_postings,
        "truncated_token_count": len(truncated),
        "truncated_tokens_sample": truncated[:100],
        "categories": categories,
        "sources": sources,
        "doc_shards": doc_shards,
        "token_buckets": token_metadata,
        "paths": {
            "docs": "data/v3/docs/",
            "tokens": "data/v2/tokens/",
            "v3_manifest": "data/v3/manifest.json",
        },
        "query_model": "Compatibility surface: v2 doc-id postings hydrate compact v3 document shards.",
    }
    _write_json(v2_root / "manifest.json", manifest, pretty=True)
    return manifest


def _refresh_v2_helper(public_dir: Path) -> None:
    """Keep the generated compatibility helper aligned with v3 tokenization."""
    path = public_dir / "jtorrent-search-v2.js"
    if not path.is_file():
        return
    source = path.read_text(encoding="utf-8")
    replacement = "const STOPWORDS = new Set(" + json.dumps(sorted(STOPWORDS), separators=(",", ":")) + ');'
    updated, count = re.subn(r"const STOPWORDS = new Set\(\[[^;]*?\]\);", replacement, source, count=1)
    if count != 1:
        raise RuntimeError("Unable to update the generated v2 compatibility helper stopword list.")
    path.write_text(updated, encoding="utf-8")


def _write_public_info_pages(public_dir: Path, status: dict[str, Any], v3_manifest: dict[str, Any]) -> None:
    generated = status.get("generated_at") or "unknown"
    index_html = f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>JTorrent Search Backend</title>
  <meta name="robots" content="noindex,follow">
  <style>body{{font-family:system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;max-width:920px;margin:2rem auto;padding:0 1rem;line-height:1.55;color:#17212b}}.grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(190px,1fr));gap:1rem}}.card{{border:1px solid #d7e0e7;border-radius:12px;padding:1rem;background:#f8fafc}}code{{background:#eef3f7;padding:.1rem .3rem;border-radius:4px}}a{{color:#006f95}}</style>
</head>
<body>
  <h1>JTorrent search backend</h1>
  <p>This GitHub Pages site publishes the static search index used by <strong>www.jtorrent.net</strong>.</p>
  <div class="grid">
    <div class="card"><strong>Search contract</strong><br>v3</div>
    <div class="card"><strong>Indexed records</strong><br>{status.get('item_count', 0):,}</div>
    <div class="card"><strong>Healthy sources</strong><br>{status.get('healthy_source_count', 0)}</div>
    <div class="card"><strong>Generated</strong><br>{generated}</div>
  </div>
  <h2>Public endpoints</h2>
  <ul>
    <li><a href="data/status.json">data/status.json</a></li>
    <li><a href="data/source-health.json">data/source-health.json</a></li>
    <li><a href="data/v3/manifest.json">data/v3/manifest.json</a></li>
    <li><a href="data/v2/manifest.json">data/v2/manifest.json</a> (compatibility)</li>
    <li><a href="data/manifest.json">data/manifest.json</a></li>
  </ul>
  <p>Search clients fetch only the required token buckets and compact document shards. They do not download the full corpus.</p>
</body>
</html>
"""
    (public_dir / "index.html").write_text(index_html, encoding="utf-8")
    base_url = "https://index.jtorrent.net"
    sitemap_urls = [
        f"{base_url}/",
        f"{base_url}/data/status.json",
        f"{base_url}/data/source-health.json",
        f"{base_url}/data/v3/manifest.json",
        f"{base_url}/data/v2/manifest.json",
    ]
    sitemap = '<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
    sitemap += "".join(f"  <url><loc>{url}</loc></url>\n" for url in sitemap_urls)
    sitemap += "</urlset>\n"
    (public_dir / "sitemap.xml").write_text(sitemap, encoding="utf-8")

def _source_health(source_rows: Any) -> list[dict[str, Any]]:
    if not isinstance(source_rows, list):
        return []
    result = []
    for source in source_rows:
        error = source.get("error")
        warnings = [str(value) for value in (source.get("warnings") or []) if str(value).strip()]
        indexed = _as_int(source.get("indexed_item_count"), 0)
        state = "failed" if error else "degraded" if warnings else "healthy" if indexed > 0 else "empty"
        result.append({
            "id": source.get("id"),
            "name": source.get("name"),
            "required": bool(source.get("required")),
            "category": source.get("category"),
            "item_count": _as_int(source.get("item_count"), 0),
            "indexed_item_count": indexed,
            "state": state,
            "error": error,
            "warnings": warnings,
        })
    return result


def _prune_legacy(public_dir: Path, root_manifest: dict[str, Any]) -> None:
    for relative in ["data/shards/full", "data/shards/compact", "data/search", "data/by-category", "data/by-source"]:
        shutil.rmtree(public_dir / relative, ignore_errors=True)
    pointer = {
        "mode": "versioned-search-pointer",
        "message": "Use data/status.json and data/v3/manifest.json. v2 remains available for compatibility.",
        "status_path": "data/status.json",
        "search_v3_manifest_path": "data/v3/manifest.json",
        "search_v2_manifest_path": "data/v2/manifest.json",
        "total_results": root_manifest.get("item_count", 0),
    }
    for name in ["search-index.json", "search-index.min.json", "search-index.summary.json"]:
        _write_json(public_dir / "data" / name, pointer, pretty=True)
    _write_json(public_dir / "data" / "shards" / "index.json", {
        "legacy_active": False,
        "full_shards": [],
        "compact_shards": [],
        "search_shards": [],
        "source_shards": root_manifest.get("sharding", {}).get("source_shards", []),
        "search_v2_manifest": "data/v2/manifest.json",
        "search_v3_manifest": "data/v3/manifest.json",
    }, pretty=True)
    sharding = root_manifest.setdefault("sharding", {})
    sharding.update({
        "legacy_active": False,
        "full_shards": [],
        "compact_shards": [],
        "search_shards": [],
        "by_category": {},
        "by_source": {},
        "search_v3_manifest": "data/v3/manifest.json",
    })


def build_search_v3(public_dir: Path, output_options: dict[str, Any] | None = None) -> dict[str, Any]:
    public_dir = Path(public_dir)
    output_options = output_options or {}
    options = output_options.get("search_v3") if isinstance(output_options.get("search_v3"), dict) else {}
    if options.get("enabled", True) is False:
        return {"enabled": False}
    max_doc_bytes = max(64 * 1024, _as_int(options.get("doc_shard_bytes"), DEFAULT_DOC_SHARD_BYTES))
    prefix_chars = max(1, min(4, _as_int(options.get("token_bucket_prefix_chars"), DEFAULT_PREFIX_CHARS)))
    max_index_tokens = max(8, _as_int(options.get("max_tokens_per_record"), DEFAULT_MAX_INDEX_TOKENS))
    max_postings = max(0, _as_int(options.get("max_postings_per_token"), DEFAULT_MAX_POSTINGS))
    max_query_tokens = max(2, _as_int(options.get("max_query_tokens"), DEFAULT_MAX_QUERY_TOKENS))

    root_manifest_path = public_dir / "data" / "manifest.json"
    root_manifest = _read_json(root_manifest_path)
    raw_docs = _load_v2_docs(public_dir)
    compact_docs = [_compact_doc(row) for row in raw_docs]
    categories = sorted({str(doc.get("category")) for doc in compact_docs if doc.get("category")})
    sources = sorted({str(doc.get("source_id")) for doc in compact_docs if doc.get("source_id")})
    category_map = {value: index for index, value in enumerate(categories)}
    source_map = {value: index for index, value in enumerate(sources)}

    postings: dict[str, list[list[int]]] = defaultdict(list)
    for doc in compact_docs:
        doc_id = int(doc["doc_id"])
        category_index = category_map.get(str(doc.get("category")), -1)
        source_index = source_map.get(str(doc.get("source_id")), -1)
        for token, score in _token_scores_for_doc(doc, max_index_tokens).items():
            postings[token].append([doc_id, category_index, source_index, score])

    doc_shards = _write_doc_shards(public_dir, compact_docs, max_doc_bytes)
    token_buckets, truncated = _write_token_buckets(public_dir, postings, prefix_chars=prefix_chars, max_postings=max_postings)

    source_rows = _read_json(public_dir / "data" / "sources.json")
    health = _source_health(source_rows)
    _write_json(public_dir / "data" / "source-health.json", {"generated_at": root_manifest.get("generated_at"), "sources": health}, pretty=True)

    v3_manifest = {
        "version": 3,
        "type": "static-token-index",
        "generated_at": root_manifest.get("generated_at"),
        "item_count": len(compact_docs),
        "doc_count": len(compact_docs),
        "token_count": len(postings),
        "tokenization_version": 3,
        "token_bucket_prefix_chars": prefix_chars,
        "max_query_tokens": max_query_tokens,
        "max_tokens_per_record": max_index_tokens,
        "max_postings_per_token": max_postings,
        "stopwords": sorted(STOPWORDS),
        "categories": categories,
        "sources": sources,
        "doc_shards": doc_shards,
        "token_buckets": token_buckets,
        "truncated_token_count": len(truncated),
        "truncated_tokens_sample": truncated[:100],
        "paths": {"status": "data/status.json", "docs": "data/v3/docs/", "tokens": "data/v3/tokens/", "source_health": "data/source-health.json"},
        "posting_format": ["doc_id", "category_index", "source_index", "static_score"],
        "query_model": "Tokenize query, filter postings by category/source indexes, combine static relevance scores, intersect with labelled OR fallback, then hydrate only compact document shards for the requested result window.",
    }
    _write_json(public_dir / "data" / "v3" / "manifest.json", v3_manifest, pretty=True)

    v2_compat = _write_v2_compatibility(
        public_dir,
        postings,
        prefix_chars=prefix_chars,
        max_postings=max_postings,
        doc_shards=doc_shards,
        categories=categories,
        sources=sources,
        generated_at=root_manifest.get("generated_at"),
    )

    healthy = sum(1 for item in health if item["state"] == "healthy")
    degraded = sum(1 for item in health if item["state"] == "degraded")
    failed = sum(1 for item in health if item["state"] == "failed")
    status = {
        "version": 1,
        "generated_at": root_manifest.get("generated_at"),
        "item_count": len(compact_docs),
        "enabled_source_count": root_manifest.get("enabled_source_count", len(health)),
        "healthy_source_count": healthy,
        "degraded_source_count": degraded,
        "failed_source_count": failed,
        "search_contract": "v3",
        "search_manifest": "data/v3/manifest.json",
        "fallback_manifest": "data/v2/manifest.json",
        "tokenization_version": 3,
    }
    _write_json(public_dir / "data" / "status.json", status, pretty=True)
    _refresh_v2_helper(public_dir)
    _write_public_info_pages(public_dir, status, v3_manifest)

    root_manifest["search_v3"] = {
        "version": 3,
        "type": v3_manifest["type"],
        "item_count": v3_manifest["item_count"],
        "manifest_path": "data/v3/manifest.json",
        "status_path": "data/status.json",
    }
    root_manifest["search_v2"] = v2_compat
    root_manifest["search_contract"] = "v3"
    root_manifest["status_path"] = "data/status.json"
    _prune_legacy(public_dir, root_manifest)
    _write_json(root_manifest_path, root_manifest, pretty=True)
    return {"enabled": True, "status": status, "manifest": v3_manifest, "v2_compat": v2_compat}
