from __future__ import annotations

import json
import re
import shutil
import unicodedata
from array import array
from pathlib import Path
from typing import Any

from .models import TorrentItem
from .normalize import scalar_text, slugify

DEFAULT_MAX_JSON_FILE_BYTES = 5 * 1024 * 1024
DEFAULT_LEGACY_MAX_ITEMS = 100_000

_TOKEN_RE = re.compile(r"[a-z0-9][a-z0-9.+_-]{1,63}")
_STOPWORDS = {
    "the", "and", "for", "from", "with", "this", "that", "are", "you", "your", "have", "has", "had",
    "all", "any", "can", "not", "but", "its", "into", "about", "download", "torrent", "torrents",
    "official", "archive", "internet", "creative", "commons", "public", "domain", "license", "metadata",
}


def _write_json(path: Path, data: Any, *, pretty: bool = True) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        if pretty:
            json.dump(data, f, ensure_ascii=False, indent=2, sort_keys=True)
        else:
            json.dump(data, f, ensure_ascii=False, separators=(",", ":"))
        f.write("\n")


def _json_bytes(data: Any, *, pretty: bool = False) -> int:
    if pretty:
        text = json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True)
    else:
        text = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    return len(text.encode("utf-8")) + 1


def _record_bytes(record: dict[str, Any]) -> int:
    return len(json.dumps(record, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))


def _as_bool(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    return str(value).strip().lower() in {"1", "true", "yes", "y", "on"}


def _as_int(value: Any, default: int) -> int:
    if value is None or value == "":
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _compact(item: dict[str, Any]) -> dict[str, Any]:
    keys = [
        "id",
        "slug",
        "title",
        "category",
        "source_id",
        "source_name",
        "source_homepage",
        "source_url",
        "details_url",
        "torrent_url",
        "magnet",
        "infohash",
        "size_bytes",
        "size",
        "seeders",
        "leechers",
        "date_added",
        "date_published",
        "license",
        "license_url",
        "copyright_status",
        "policy_scope",
        "mirrors",
        "first_seen",
        "last_seen",
        "description",
        "tags",
        "fetched_at",
    ]
    return {k: item.get(k) for k in keys if item.get(k) not in [None, "", [], {}]}


def _search_record(item: dict[str, Any], *, full_shard: str | None, compact_shard: str | None) -> dict[str, Any]:
    description = item.get("description")
    if isinstance(description, str) and len(description) > 280:
        description = description[:277].rstrip() + "..."
    record = {
        "id": item.get("id"),
        "slug": item.get("slug"),
        "title": item.get("title"),
        "normalized_title": item.get("normalized_title"),
        "category": item.get("category"),
        "source_id": item.get("source_id"),
        "source_name": item.get("source_name"),
        "source_homepage": item.get("source_homepage"),
        "size_bytes": item.get("size_bytes"),
        "size": item.get("size"),
        "seeders": item.get("seeders"),
        "leechers": item.get("leechers"),
        "date_added": item.get("date_added"),
        "date_published": item.get("date_published"),
        "copyright_status": item.get("copyright_status"),
        "policy_scope": item.get("policy_scope"),
        "mirrors": item.get("mirrors"),
        "first_seen": item.get("first_seen"),
        "last_seen": item.get("last_seen"),
        "tags": item.get("tags"),
        "description": description,
        "has_magnet": bool(item.get("magnet")),
        "has_torrent_url": bool(item.get("torrent_url")),
        "full_shard": full_shard,
        "compact_shard": compact_shard,
    }
    return {k: v for k, v in record.items() if v not in [None, "", [], {}]}


def _public_url(base_url: str, rel_path: str) -> str | None:
    if not base_url:
        return None
    return f"{base_url.rstrip('/')}/{rel_path.lstrip('/')}"


def _row_values(rows: list[dict[str, Any]], key: str) -> list[str]:
    values = {str(row.get(key)) for row in rows if row.get(key) not in [None, ""]}
    return sorted(values)


def _shard_metadata(
    *,
    shard_id: str,
    rel_path: str,
    rows: list[dict[str, Any]],
    byte_size: int,
    base_url: str,
    oversized_records: int = 0,
) -> dict[str, Any]:
    metadata: dict[str, Any] = {
        "id": shard_id,
        "path": rel_path,
        "count": len(rows),
        "bytes": byte_size,
        "categories": _row_values(rows, "category"),
        "sources": _row_values(rows, "source_id"),
    }
    doc_ids = [row.get("doc_id") for row in rows if isinstance(row.get("doc_id"), int)]
    if doc_ids:
        metadata["start_doc_id"] = min(doc_ids)
        metadata["end_doc_id"] = max(doc_ids)
    url = _public_url(base_url, rel_path)
    if url:
        metadata["url"] = url
    if oversized_records:
        metadata["oversized_records"] = oversized_records
    return metadata


def _write_record_shards(
    output_dir: Path,
    records: list[dict[str, Any]],
    *,
    rel_dir: str,
    prefix: str,
    max_file_bytes: int,
    base_url: str,
) -> tuple[list[dict[str, Any]], list[str | None]]:
    """Write records into JSON-array shards capped near max_file_bytes."""

    if not records:
        return [], []

    rel_dir_path = Path(rel_dir)
    target_dir = output_dir / rel_dir_path
    target_dir.mkdir(parents=True, exist_ok=True)

    shards: list[dict[str, Any]] = []
    assignments: list[str | None] = [None] * len(records)
    current_rows: list[dict[str, Any]] = []
    current_indices: list[int] = []
    current_size = 3
    shard_number = 1

    def finalize(rows: list[dict[str, Any]], indices: list[int], *, oversized_records: int = 0) -> None:
        nonlocal shard_number
        if not rows:
            return
        shard_id = f"{prefix}-{shard_number:04d}"
        rel_path = (rel_dir_path / f"{shard_id}.json").as_posix()
        path = output_dir / rel_path
        _write_json(path, rows, pretty=False)
        byte_size = path.stat().st_size
        for index in indices:
            assignments[index] = rel_path
        shards.append(
            _shard_metadata(
                shard_id=shard_id,
                rel_path=rel_path,
                rows=rows,
                byte_size=byte_size,
                base_url=base_url,
                oversized_records=oversized_records,
            )
        )
        shard_number += 1

    for index, record in enumerate(records):
        item_size = _record_bytes(record)
        item_array_size = item_size + 3
        if item_array_size > max_file_bytes:
            finalize(current_rows, current_indices)
            current_rows = []
            current_indices = []
            current_size = 3
            finalize([record], [index], oversized_records=1)
            continue

        next_size = current_size + item_size + (1 if current_rows else 0)
        if current_rows and next_size > max_file_bytes:
            finalize(current_rows, current_indices)
            current_rows = []
            current_indices = []
            current_size = 3
            next_size = current_size + item_size

        current_rows.append(record)
        current_indices.append(index)
        current_size = next_size

    finalize(current_rows, current_indices)
    return shards, assignments


def _write_size_limited_json(
    path: Path,
    data: Any,
    *,
    pretty: bool,
    max_file_bytes: int,
    stub: dict[str, Any],
) -> dict[str, Any]:
    full_size = _json_bytes(data, pretty=pretty)
    if full_size <= max_file_bytes:
        _write_json(path, data, pretty=pretty)
        return {"path": path.name, "written": True, "bytes": path.stat().st_size, "mode": "inline"}

    _write_json(path, stub, pretty=True)
    return {
        "path": path.name,
        "written": False,
        "bytes": path.stat().st_size,
        "would_have_been_bytes": full_size,
        "mode": stub.get("mode", "sharded-stub"),
    }


def _write_pointer_json(path: Path, stub: dict[str, Any]) -> dict[str, Any]:
    _write_json(path, stub, pretty=True)
    return {"path": path.name, "written": False, "bytes": path.stat().st_size, "mode": stub.get("mode", "v2-only")}


def _write_group_files(
    output_dir: Path,
    groups: dict[str, list[dict[str, Any]]],
    *,
    group_dir: str,
    group_label: str,
    max_file_bytes: int,
    base_url: str,
) -> dict[str, dict[str, Any]]:
    group_manifest: dict[str, dict[str, Any]] = {}
    for group_name, rows in sorted(groups.items()):
        group_file_rel = f"data/{group_dir}/{group_name}.json"
        group_file = output_dir / group_file_rel
        byte_size = _json_bytes(rows, pretty=False)
        if byte_size <= max_file_bytes:
            _write_json(group_file, rows, pretty=False)
            entry: dict[str, Any] = {
                group_label: group_name,
                "mode": "inline",
                "path": group_file_rel,
                "count": len(rows),
                "bytes": group_file.stat().st_size,
            }
            url = _public_url(base_url, group_file_rel)
            if url:
                entry["url"] = url
        else:
            shard_rel_dir = f"data/{group_dir}/{group_name}"
            shards, _assignments = _write_record_shards(
                output_dir,
                rows,
                rel_dir=shard_rel_dir,
                prefix="part",
                max_file_bytes=max_file_bytes,
                base_url=base_url,
            )
            stub = {
                "mode": "sharded",
                group_label: group_name,
                "count": len(rows),
                "max_file_bytes": max_file_bytes,
                "parts": shards,
            }
            _write_json(group_file, stub, pretty=True)
            entry = {
                group_label: group_name,
                "mode": "sharded",
                "path": group_file_rel,
                "count": len(rows),
                "bytes": group_file.stat().st_size,
                "parts": shards,
            }
            url = _public_url(base_url, group_file_rel)
            if url:
                entry["url"] = url
        group_manifest[group_name] = entry
    return group_manifest


def _normalize_token_text(value: Any) -> str:
    text = unicodedata.normalize("NFKD", scalar_text(value)).encode("ascii", "ignore").decode("ascii")
    return text.lower()


def _tokens_for_item(item: dict[str, Any], *, max_tokens: int) -> list[str]:
    text_parts = [
        item.get("title"),
        item.get("normalized_title"),
        item.get("category"),
        item.get("source_id"),
        item.get("source_name"),
        " ".join(item.get("tags") or []),
        item.get("description"),
    ]
    text = _normalize_token_text(" ".join(scalar_text(part) for part in text_parts if part not in [None, "", [], {}]))
    seen: set[str] = set()
    tokens: list[str] = []
    for match in _TOKEN_RE.finditer(text):
        token = match.group(0).strip("._-+")
        if len(token) < 2 or token in _STOPWORDS:
            continue
        if token.isdigit() and len(token) < 4:
            continue
        if token not in seen:
            seen.add(token)
            tokens.append(token)
        if len(tokens) >= max_tokens:
            break
    return tokens


def _token_bucket(token: str, *, prefix_chars: int) -> str:
    cleaned = re.sub(r"[^a-z0-9]", "", token.lower())
    if not cleaned:
        return "__"
    return cleaned[:prefix_chars].ljust(prefix_chars, "_")


def _scalable_doc_record(item: dict[str, Any], *, doc_id: int, description_max_chars: int) -> dict[str, Any]:
    description = item.get("description")
    if isinstance(description, str) and description_max_chars >= 0 and len(description) > description_max_chars:
        description = description[: max(0, description_max_chars - 1)].rstrip() + "…"
    record = {
        "doc_id": doc_id,
        "id": item.get("id"),
        "slug": item.get("slug"),
        "title": item.get("title"),
        "category": item.get("category"),
        "source_id": item.get("source_id"),
        "source_name": item.get("source_name"),
        "source_url": item.get("source_url") or item.get("details_url"),
        "details_url": item.get("details_url"),
        "torrent_url": item.get("torrent_url"),
        "magnet": item.get("magnet"),
        "infohash": item.get("infohash"),
        "size": item.get("size"),
        "size_bytes": item.get("size_bytes"),
        "seeders": item.get("seeders"),
        "leechers": item.get("leechers"),
        "date_added": item.get("date_added") or item.get("date_published"),
        "copyright_status": item.get("copyright_status"),
        "license": item.get("license"),
        "license_url": item.get("license_url"),
        "tags": item.get("tags"),
        "description": description,
    }
    return {k: v for k, v in record.items() if v not in [None, "", [], {}]}


def _write_token_buckets(
    output_dir: Path,
    token_postings: dict[str, array],
    *,
    prefix_chars: int,
    max_postings_per_token: int,
    base_url: str,
) -> tuple[list[dict[str, Any]], list[str], int]:
    buckets: dict[str, dict[str, list[int]]] = {}
    truncated_tokens: list[str] = []
    total_terms = 0
    for token in sorted(token_postings):
        postings = token_postings[token]
        if not postings:
            continue
        total_terms += 1
        values = postings.tolist()
        if max_postings_per_token and len(values) > max_postings_per_token:
            values = values[:max_postings_per_token]
            truncated_tokens.append(token)
        bucket = _token_bucket(token, prefix_chars=prefix_chars)
        buckets.setdefault(bucket, {})[token] = values

    truncated_set = set(truncated_tokens)
    metadata: list[dict[str, Any]] = []
    token_dir = output_dir / "data" / "v2" / "tokens"
    token_dir.mkdir(parents=True, exist_ok=True)
    for bucket, token_map in sorted(buckets.items()):
        rel_path = f"data/v2/tokens/{bucket}.json"
        payload = {
            "bucket": bucket,
            "token_count": len(token_map),
            "tokens": token_map,
            "truncated_tokens": [t for t in token_map if t in truncated_set],
        }
        path = output_dir / rel_path
        _write_json(path, payload, pretty=False)
        entry: dict[str, Any] = {
            "id": bucket,
            "path": rel_path,
            "bytes": path.stat().st_size,
            "token_count": len(token_map),
        }
        url = _public_url(base_url, rel_path)
        if url:
            entry["url"] = url
        metadata.append(entry)
    return metadata, truncated_tokens, total_terms


def _write_scalable_search_v2(
    output_dir: Path,
    item_dicts: list[dict[str, Any]],
    *,
    max_file_bytes: int,
    base_url: str,
    options: dict[str, Any],
) -> dict[str, Any]:
    v2_opts = (options.get("scalable_search") or {}) if isinstance(options.get("scalable_search"), dict) else {}
    prefix_chars = max(1, min(4, _as_int(v2_opts.get("token_bucket_prefix_chars"), 2)))
    max_tokens_per_record = max(4, _as_int(v2_opts.get("max_tokens_per_record"), 40))
    max_postings_per_token = max(0, _as_int(v2_opts.get("max_postings_per_token"), 50_000))
    description_max_chars = max(-1, _as_int(v2_opts.get("description_max_chars"), 240))

    doc_records: list[dict[str, Any]] = []
    token_postings: dict[str, array] = {}
    for doc_id, item in enumerate(item_dicts):
        doc_records.append(_scalable_doc_record(item, doc_id=doc_id, description_max_chars=description_max_chars))
        for token in _tokens_for_item(item, max_tokens=max_tokens_per_record):
            posting = token_postings.get(token)
            if posting is None:
                posting = array("I")
                token_postings[token] = posting
            # Keep one extra posting so we can mark the token as truncated.
            if not max_postings_per_token or len(posting) <= max_postings_per_token:
                posting.append(doc_id)

    doc_shards, _doc_assignments = _write_record_shards(
        output_dir,
        doc_records,
        rel_dir="data/v2/docs",
        prefix="docs",
        max_file_bytes=max_file_bytes,
        base_url=base_url,
    )
    token_buckets, truncated_tokens, token_count = _write_token_buckets(
        output_dir,
        token_postings,
        prefix_chars=prefix_chars,
        max_postings_per_token=max_postings_per_token,
        base_url=base_url,
    )

    v2_manifest: dict[str, Any] = {
        "version": 2,
        "type": "static-token-index",
        "item_count": len(item_dicts),
        "doc_count": len(doc_records),
        "token_count": token_count,
        "token_bucket_prefix_chars": prefix_chars,
        "max_tokens_per_record": max_tokens_per_record,
        "max_postings_per_token": max_postings_per_token,
        "truncated_token_count": len(truncated_tokens),
        "truncated_tokens_sample": truncated_tokens[:100],
        "doc_shards": doc_shards,
        "token_buckets": token_buckets,
        "paths": {
            "docs": "data/v2/docs/",
            "tokens": "data/v2/tokens/",
            "client": "jtorrent-search-v2.js",
        },
        "query_model": "tokenize query, fetch matching token bucket JSON files, intersect/union doc_id postings, then hydrate doc_ids from doc_shards.",
    }
    _write_json(output_dir / "data" / "v2" / "manifest.json", v2_manifest, pretty=True)
    _write_scalable_client_js(output_dir)
    return v2_manifest


def _write_scalable_client_js(output_dir: Path) -> None:
    js = r'''/* JTorrent static token-search client v2. Generated by the backend build. */
(function(){
  const STOPWORDS = new Set(["the","and","for","from","with","this","that","are","you","your","have","has","had","all","any","can","not","but","its","into","about","download","torrent","torrents","official","archive","internet","creative","commons","public","domain","license","metadata"]);
  const TOKEN_RE = /[a-z0-9][a-z0-9.+_-]{1,63}/g;
  const manifestCache = new Map();
  const jsonCache = new Map();

  function joinUrl(base, path){ return String(base || '').replace(/\/+$/, '') + '/' + String(path || '').replace(/^\/+/, ''); }
  async function fetchJson(url){
    if (jsonCache.has(url)) return jsonCache.get(url);
    const promise = fetch(url, {cache:'no-store', mode:'cors', credentials:'omit'}).then(r => { if(!r.ok) throw new Error('HTTP '+r.status+' '+url); return r.json(); });
    jsonCache.set(url, promise);
    return promise;
  }
  function normalize(text){ return String(text || '').normalize('NFKD').replace(/[\u0300-\u036f]/g, '').toLowerCase(); }
  function tokenize(text){
    const out = [];
    const seen = new Set();
    const normalized = normalize(text);
    let match;
    while ((match = TOKEN_RE.exec(normalized))) {
      const token = match[0].replace(/^[._+-]+|[._+-]+$/g, '');
      if (token.length < 2 || STOPWORDS.has(token)) continue;
      if (/^\d+$/.test(token) && token.length < 4) continue;
      if (!seen.has(token)) { seen.add(token); out.push(token); }
      if (out.length >= 8) break;
    }
    return out;
  }
  function bucketFor(token, prefixChars){ return token.replace(/[^a-z0-9]/g, '').slice(0, prefixChars).padEnd(prefixChars, '_') || '__'; }
  async function loadManifest(baseUrl){
    const key = String(baseUrl || '').replace(/\/+$/, '');
    if (manifestCache.has(key)) return manifestCache.get(key);
    const promise = (async () => {
      const manifest = await fetchJson(joinUrl(key, 'data/manifest.json'));
      const v2 = manifest.search_v2 || await fetchJson(joinUrl(key, 'data/v2/manifest.json'));
      return { manifest, v2 };
    })();
    manifestCache.set(key, promise);
    return promise;
  }
  function intersect(a, b){
    const small = a.length <= b.length ? a : b;
    const largeSet = new Set(a.length <= b.length ? b : a);
    return small.filter(x => largeSet.has(x));
  }
  function unionMany(lists){
    const seen = new Set();
    const out = [];
    for (const list of lists) for (const value of list) if (!seen.has(value)) { seen.add(value); out.push(value); }
    return out;
  }
  function shardForDoc(v2, docId){ return (v2.doc_shards || []).find(s => docId >= s.start_doc_id && docId <= s.end_doc_id); }
  async function hydrateDocs(baseUrl, v2, docIds){
    const byShard = new Map();
    for (const id of docIds) {
      const shard = shardForDoc(v2, id);
      if (!shard) continue;
      const key = shard.path;
      if (!byShard.has(key)) byShard.set(key, []);
      byShard.get(key).push(id);
    }
    const docs = [];
    for (const [path, ids] of byShard.entries()) {
      const wanted = new Set(ids);
      const rows = await fetchJson(joinUrl(baseUrl, path));
      for (const row of rows) if (wanted.has(row.doc_id)) docs.push(row);
    }
    const order = new Map(docIds.map((id, i) => [id, i]));
    docs.sort((a,b) => (order.get(a.doc_id) ?? 999999999) - (order.get(b.doc_id) ?? 999999999));
    return docs;
  }
  async function search(options){
    const baseUrl = options.baseUrl || options.base || 'https://index.jtorrent.net';
    const query = options.query || '';
    const limit = Math.max(1, Math.min(100, options.limit || 25));
    const mode = options.mode || 'and';
    const {v2} = await loadManifest(baseUrl);
    const tokens = tokenize(query);
    if (!tokens.length) return {query, tokens, total:0, results:[]};
    const prefixChars = v2.token_bucket_prefix_chars || 2;
    const postings = [];
    const loadedBuckets = new Map();
    for (const token of tokens) {
      const bucket = bucketFor(token, prefixChars);
      if (!loadedBuckets.has(bucket)) loadedBuckets.set(bucket, fetchJson(joinUrl(baseUrl, `data/v2/tokens/${bucket}.json`)).catch(() => ({tokens:{}})));
      const bucketPayload = await loadedBuckets.get(bucket);
      postings.push((bucketPayload.tokens && bucketPayload.tokens[token]) || []);
    }
    let ids = mode === 'or' ? unionMany(postings) : postings.reduce((acc, list) => acc === null ? list : intersect(acc, list), null) || [];
    if (!ids.length && mode !== 'or') ids = unionMany(postings);
    ids = ids.slice(0, Math.max(limit * 5, limit));
    const docs = await hydrateDocs(baseUrl, v2, ids);
    const qset = new Set(tokens);
    docs.forEach(doc => {
      const title = normalize(doc.title || '');
      let score = 0;
      for (const t of qset) if (title.includes(t)) score += 10;
      if (doc.seeders) score += Math.min(5, Math.log10(1 + Number(doc.seeders)));
      doc._score = score;
    });
    docs.sort((a,b) => (b._score || 0) - (a._score || 0) || String(b.date_added || '').localeCompare(String(a.date_added || '')));
    return {query, tokens, total: ids.length, results: docs.slice(0, limit)};
  }
  window.JTorrentSearchV2 = { search, loadManifest, tokenize };
})();
'''
    (output_dir / "jtorrent-search-v2.js").write_text(js, encoding="utf-8")


def _legacy_pointer_stub(kind: str, item_count: int, max_file_bytes: int) -> dict[str, Any]:
    return {
        "mode": "v2-only",
        "kind": kind,
        "message": "This dataset is too large for legacy browser-wide JSON shards. Use data/v2/manifest.json and jtorrent-search-v2.js.",
        "manifest_path": "data/manifest.json",
        "search_v2_manifest_path": "data/v2/manifest.json",
        "total_results": item_count,
        "max_file_bytes": max_file_bytes,
    }


def write_public(
    output_dir: Path,
    items: list[TorrentItem],
    sources: list[dict[str, Any]],
    manifest: dict[str, Any],
    *,
    max_file_bytes: int = DEFAULT_MAX_JSON_FILE_BYTES,
    output_options: dict[str, Any] | None = None,
) -> None:
    if output_dir.exists():
        shutil.rmtree(output_dir)
    (output_dir / "data").mkdir(parents=True, exist_ok=True)
    (output_dir / ".nojekyll").write_text("", encoding="utf-8")

    options = output_options or {}
    max_file_bytes = max(1024, int(max_file_bytes or DEFAULT_MAX_JSON_FILE_BYTES))
    item_dicts = [i.to_dict() for i in items]
    compact = [_compact(i) for i in item_dicts]
    base_url = str(manifest.get("base_url") or "").rstrip("/")

    scalable_enabled = _as_bool((options.get("scalable_search") or {}).get("enabled") if isinstance(options.get("scalable_search"), dict) else options.get("scalable_search"), True)
    legacy_shards_enabled = _as_bool(options.get("legacy_shards"), True)
    legacy_max_items = max(0, _as_int(options.get("legacy_max_items"), DEFAULT_LEGACY_MAX_ITEMS))
    legacy_active = legacy_shards_enabled and (not legacy_max_items or len(item_dicts) <= legacy_max_items)
    group_files_enabled = _as_bool(options.get("group_files"), legacy_active and len(item_dicts) <= legacy_max_items)

    v2_manifest: dict[str, Any] | None = None
    if scalable_enabled:
        v2_manifest = _write_scalable_search_v2(
            output_dir,
            item_dicts,
            max_file_bytes=max_file_bytes,
            base_url=base_url,
            options=options,
        )
        manifest["search_v2"] = v2_manifest

    if legacy_active:
        full_shards, full_assignments = _write_record_shards(
            output_dir,
            item_dicts,
            rel_dir="data/shards/full",
            prefix="full",
            max_file_bytes=max_file_bytes,
            base_url=base_url,
        )
        compact_shards, compact_assignments = _write_record_shards(
            output_dir,
            compact,
            rel_dir="data/shards/compact",
            prefix="compact",
            max_file_bytes=max_file_bytes,
            base_url=base_url,
        )
        search_records = [
            _search_record(item, full_shard=full_assignments[index], compact_shard=compact_assignments[index])
            for index, item in enumerate(item_dicts)
        ]
        search_shards, _search_assignments = _write_record_shards(
            output_dir,
            search_records,
            rel_dir="data/search",
            prefix="search",
            max_file_bytes=max_file_bytes,
            base_url=base_url,
        )

        search_index_stub = {
            "mode": "sharded",
            "message": "The compact index exceeded max_file_bytes. Load data/manifest.json, then load manifest.sharding.compact_shards or manifest.sharding.search_shards.",
            "manifest_path": "data/manifest.json",
            "search_v2_manifest_path": "data/v2/manifest.json" if scalable_enabled else None,
            "total_results": len(compact),
            "max_file_bytes": max_file_bytes,
            "compact_shards": compact_shards,
            "search_shards": search_shards,
        }
        full_index_stub = {
            "mode": "sharded",
            "message": "The full index exceeded max_file_bytes. Load data/manifest.json, then load manifest.sharding.full_shards.",
            "manifest_path": "data/manifest.json",
            "search_v2_manifest_path": "data/v2/manifest.json" if scalable_enabled else None,
            "total_results": len(item_dicts),
            "max_file_bytes": max_file_bytes,
            "full_shards": full_shards,
        }

        full_index_file = _write_size_limited_json(
            output_dir / "data" / "search-index.json",
            item_dicts,
            pretty=True,
            max_file_bytes=max_file_bytes,
            stub=full_index_stub,
        )
        compact_index_file = _write_size_limited_json(
            output_dir / "data" / "search-index.min.json",
            compact,
            pretty=False,
            max_file_bytes=max_file_bytes,
            stub=search_index_stub,
        )
        summary_index_file = _write_size_limited_json(
            output_dir / "data" / "search-index.summary.json",
            search_records,
            pretty=False,
            max_file_bytes=max_file_bytes,
            stub={
                "mode": "sharded",
                "message": "The lightweight search index exceeded max_file_bytes. Load data/manifest.json, then load manifest.sharding.search_shards.",
                "manifest_path": "data/manifest.json",
                "search_v2_manifest_path": "data/v2/manifest.json" if scalable_enabled else None,
                "total_results": len(search_records),
                "max_file_bytes": max_file_bytes,
                "search_shards": search_shards,
            },
        )
    else:
        full_shards = []
        compact_shards = []
        search_shards = []
        full_index_file = _write_pointer_json(output_dir / "data" / "search-index.json", _legacy_pointer_stub("full", len(item_dicts), max_file_bytes))
        compact_index_file = _write_pointer_json(output_dir / "data" / "search-index.min.json", _legacy_pointer_stub("compact", len(compact), max_file_bytes))
        summary_index_file = _write_pointer_json(output_dir / "data" / "search-index.summary.json", _legacy_pointer_stub("summary", len(item_dicts), max_file_bytes))

    source_shards: list[dict[str, Any]] = []
    sources_size = _json_bytes(sources, pretty=True)
    if sources_size <= max_file_bytes:
        _write_json(output_dir / "data" / "sources.json", sources, pretty=True)
        sources_file = {"path": "sources.json", "written": True, "bytes": (output_dir / "data" / "sources.json").stat().st_size, "mode": "inline"}
    else:
        source_shards, _source_assignments = _write_record_shards(
            output_dir,
            sources,
            rel_dir="data/sources",
            prefix="sources",
            max_file_bytes=max_file_bytes,
            base_url=base_url,
        )
        _write_json(
            output_dir / "data" / "sources.json",
            {
                "mode": "sharded",
                "message": "The source metadata exceeded max_file_bytes. Load data/manifest.json, then load manifest.sharding.source_shards.",
                "manifest_path": "data/manifest.json",
                "source_shards": source_shards,
            },
            pretty=True,
        )
        sources_file = {
            "path": "sources.json",
            "written": False,
            "bytes": (output_dir / "data" / "sources.json").stat().st_size,
            "would_have_been_bytes": sources_size,
            "mode": "sharded-stub",
        }

    by_category: dict[str, list[dict[str, Any]]] = {}
    by_source: dict[str, list[dict[str, Any]]] = {}
    if group_files_enabled:
        for item in compact:
            by_category.setdefault(slugify(item.get("category"), "uncategorized"), []).append(item)
            by_source.setdefault(slugify(item.get("source_id"), "unknown"), []).append(item)
        category_files = _write_group_files(
            output_dir,
            by_category,
            group_dir="by-category",
            group_label="category",
            max_file_bytes=max_file_bytes,
            base_url=base_url,
        )
        source_files = _write_group_files(
            output_dir,
            by_source,
            group_dir="by-source",
            group_label="source",
            max_file_bytes=max_file_bytes,
            base_url=base_url,
        )
    else:
        category_files = {}
        source_files = {}

    shard_index = {
        "max_file_bytes": max_file_bytes,
        "legacy_active": legacy_active,
        "full_shards": full_shards,
        "compact_shards": compact_shards,
        "search_shards": search_shards,
        "source_shards": source_shards,
        "search_v2_manifest": "data/v2/manifest.json" if scalable_enabled else None,
    }
    _write_json(output_dir / "data" / "shards" / "index.json", shard_index, pretty=True)

    manifest["max_json_file_bytes"] = max_file_bytes
    manifest["files"] = {
        "search_index": full_index_file,
        "search_index_min": compact_index_file,
        "search_index_summary": summary_index_file,
        "sources": sources_file,
        "shard_index": {
            "path": "shards/index.json",
            "bytes": (output_dir / "data" / "shards" / "index.json").stat().st_size,
        },
    }
    if scalable_enabled:
        manifest["files"]["search_v2_manifest"] = {
            "path": "v2/manifest.json",
            "bytes": (output_dir / "data" / "v2" / "manifest.json").stat().st_size,
            "mode": "static-token-index",
            "written": True,
        }
    manifest["sharding"] = {
        "enabled": True,
        "strategy": "byte-size+token-index-v2",
        "legacy_active": legacy_active,
        "legacy_max_items": legacy_max_items,
        "group_files_enabled": group_files_enabled,
        "max_file_bytes": max_file_bytes,
        "full_shards": full_shards,
        "compact_shards": compact_shards,
        "search_shards": search_shards,
        "source_shards": source_shards,
        "by_category": category_files,
        "by_source": source_files,
        "search_v2_manifest": "data/v2/manifest.json" if scalable_enabled else None,
        "notes": [
            "Legacy full/compact/search shards remain only while item_count is <= legacy_max_items.",
            "For large datasets, use the v2 static token index: data/v2/manifest.json plus data/v2/tokens/*.json and data/v2/docs/*.json.",
            "The v2 index avoids loading every search shard into the browser for each query.",
        ],
    }

    _write_json(output_dir / "data" / "manifest.json", manifest, pretty=True)

    custom_domain = manifest.get("custom_domain")
    if custom_domain:
        (output_dir / "CNAME").write_text(str(custom_domain).strip() + "\n", encoding="utf-8")

    categories = sorted(by_category) if group_files_enabled else sorted(manifest.get("counts_by_category") or {})
    sources_sorted = sorted(by_source) if group_files_enabled else sorted(manifest.get("counts_by_source") or {})
    index_html = f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>JTorrent Torrent Aggregator Index</title>
  <style>
    body {{ font-family: system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; margin: 2rem; line-height: 1.5; max-width: 980px; }}
    code, pre {{ background: #f4f4f4; padding: .15rem .3rem; border-radius: .25rem; }}
    .grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); gap: 1rem; }}
    .card {{ border: 1px solid #ddd; border-radius: .75rem; padding: 1rem; }}
    a {{ word-break: break-word; }}
  </style>
</head>
<body>
  <h1>JTorrent Torrent Aggregator Index</h1>
  <p>This is the static, scheduled search index for <strong>{manifest.get('frontend_origin') or 'JTorrent'}</strong>.</p>
  <div class="grid">
    <div class="card"><strong>Items</strong><br>{manifest.get('item_count')}</div>
    <div class="card"><strong>Sources enabled</strong><br>{manifest.get('enabled_source_count')}</div>
    <div class="card"><strong>Generated</strong><br>{manifest.get('generated_at')}</div>
    <div class="card"><strong>Legacy shards</strong><br>{'active' if legacy_active else 'v2 only'}</div>
  </div>
  <h2>JSON endpoints</h2>
  <ul>
    <li><a href="data/manifest.json">data/manifest.json</a></li>
    <li><a href="data/v2/manifest.json">data/v2/manifest.json</a> scalable token-search manifest</li>
    <li><a href="jtorrent-search-v2.js">jtorrent-search-v2.js</a> browser client for large static search</li>
    <li><a href="data/search-index.min.json">data/search-index.min.json</a> legacy compact endpoint; becomes a v2 pointer if too large</li>
    <li><a href="data/search-index.summary.json">data/search-index.summary.json</a> legacy lightweight endpoint; becomes a v2 pointer if too large</li>
    <li><a href="data/search-index.json">data/search-index.json</a> legacy full endpoint; becomes a v2 pointer if too large</li>
    <li><a href="data/sources.json">data/sources.json</a></li>
    <li><a href="data/shards/index.json">data/shards/index.json</a></li>
  </ul>
  <h2>Frontend example</h2>
  <pre><code>&lt;script src="{base_url}/jtorrent-search-v2.js"&gt;&lt;/script&gt;
&lt;script&gt;
const results = await window.JTorrentSearchV2.search({{ baseUrl: '{base_url}', query: 'ubuntu', limit: 20 }});
console.log(results.results);
&lt;/script&gt;</code></pre>
  <h2>Categories</h2>
  <ul>{''.join(f'<li>{category}</li>' for category in categories)}</ul>
  <h2>Sources</h2>
  <ul>{''.join(f'<li>{source}</li>' for source in sources_sorted)}</ul>
</body>
</html>
"""
    (output_dir / "index.html").write_text(index_html, encoding="utf-8")
    (output_dir / "robots.txt").write_text("User-agent: *\nAllow: /\n", encoding="utf-8")
    sitemap_urls = [base_url or ""]
    if base_url:
        sitemap_urls.extend(
            [
                f"{base_url}/data/manifest.json",
                f"{base_url}/data/v2/manifest.json",
                f"{base_url}/data/sources.json",
            ]
        )
    sitemap = "<?xml version=\"1.0\" encoding=\"UTF-8\"?>\n<urlset xmlns=\"http://www.sitemaps.org/schemas/sitemap/0.9\">\n"
    sitemap += "".join(f"  <url><loc>{url}</loc></url>\n" for url in sitemap_urls if url)
    sitemap += "</urlset>\n"
    (output_dir / "sitemap.xml").write_text(sitemap, encoding="utf-8")
