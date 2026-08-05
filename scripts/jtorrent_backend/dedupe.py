from __future__ import annotations

from urllib.parse import urlparse, urlunparse

from .models import TorrentItem
from .normalize import normalize_item


def _norm_url(url: str | None) -> str | None:
    if not url:
        return None
    parsed = urlparse(url.strip())
    scheme = parsed.scheme.lower()
    netloc = parsed.netloc.lower()
    path = parsed.path.rstrip("/")
    return urlunparse((scheme, netloc, path, "", parsed.query, ""))


def dedupe_key(item: TorrentItem) -> str:
    if item.infohash:
        return f"infohash:{item.infohash.lower()}"
    if item.magnet:
        return f"magnet:{item.magnet.lower()}"
    for url in [item.torrent_url, item.details_url, item.source_url]:
        n = _norm_url(url)
        if n:
            return f"url:{n}"
    title_size = f"{item.normalized_title or item.title}|{item.size_bytes or item.size or ''}"
    return f"title-size:{title_size.lower()}"


def _merge_lists(a: list, b: list) -> list:
    result = list(a or [])
    for item in b or []:
        if item not in result:
            result.append(item)
    return result


def _mirror(item: TorrentItem) -> dict:
    return {
        "source_id": item.source_id,
        "source_name": item.source_name,
        "source_url": item.source_url,
        "details_url": item.details_url,
        "torrent_url": item.torrent_url,
        "magnet": item.magnet,
    }


def merge_items(existing: TorrentItem, incoming: TorrentItem) -> TorrentItem:
    for field in existing.__dataclass_fields__:
        if field in {"tags", "files", "trackers", "mirrors", "raw"}:
            continue
        if getattr(existing, field) in [None, "", []] and getattr(incoming, field) not in [None, "", []]:
            setattr(existing, field, getattr(incoming, field))
    existing.tags = _merge_lists(existing.tags, incoming.tags)
    existing.trackers = _merge_lists(existing.trackers, incoming.trackers)
    existing.files = existing.files or incoming.files
    existing.raw.update({k: v for k, v in incoming.raw.items() if k not in existing.raw})
    for m in [_mirror(existing), _mirror(incoming), *incoming.mirrors]:
        if any(m == known for known in existing.mirrors):
            continue
        if any(m.values()):
            existing.mirrors.append(m)
    return normalize_item(existing)


def dedupe_items(items: list[TorrentItem]) -> list[TorrentItem]:
    merged: dict[str, TorrentItem] = {}
    for raw in items:
        item = normalize_item(raw)
        key = dedupe_key(item)
        if key in merged:
            merged[key] = merge_items(merged[key], item)
        else:
            item.mirrors = [_mirror(item)] if any(_mirror(item).values()) else []
            merged[key] = item
    return list(merged.values())
