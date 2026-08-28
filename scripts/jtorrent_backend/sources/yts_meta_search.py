from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import urlencode

from ..catalog import CatalogStore
from ..models import TorrentItem
from ..normalize import clean_tags, slugify
from .base import SourceAdapter


def _number(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _fresh(value: Any, days: int) -> bool:
    if not value:
        return False
    try:
        timestamp = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return False
    return timestamp >= datetime.now(timezone.utc) - timedelta(days=days)


class YtsMetaSearchSource(SourceAdapter):
    def _payload(self, params: dict[str, Any]) -> dict[str, Any]:
        base = str(self.source.get("source_homepage") or "https://en.yts-official.com/")
        payload = self.http.get(f"{base}?{urlencode(params)}").json()
        if not isinstance(payload, dict):
            raise RuntimeError("YTS meta-search returned a non-object response")
        return payload

    def _item(self, movie: dict[str, Any], hit: dict[str, Any]) -> TorrentItem | None:
        title = str(hit.get("title") or "").strip()
        infohash = str(hit.get("hash") or "").strip().lower()
        magnet = str(hit.get("magnetUrl") or "").strip()
        if not title or (not infohash and not magnet):
            return None
        provider = str(hit.get("source") or "Unknown provider").strip()
        movie_title = str(movie.get("title") or movie.get("original_title") or "").strip()
        base = self.base_item()
        base.update({
            "title": title,
            "category": "movies",
            "source_name": f"YTS Meta Search · {provider}",
            "source_url": self.source.get("source_homepage"),
            "details_url": self.source.get("source_homepage"),
            "magnet": magnet or f"magnet:?xt=urn:btih:{infohash}",
            "infohash": infohash or None,
            "hash_source": "yts-meta-search",
            "size_bytes": _number(hit.get("bytes")),
            "seeders": _number(hit.get("seeds")),
            "leechers": _number(hit.get("peers")),
            "description": str(movie.get("overview") or "").strip() or None,
            "date_published": str(movie.get("release_date") or "").strip() or None,
            "tags": clean_tags([*self.source.get("tags", []), slugify(provider, fallback="provider")]),
            "raw": {
                "provider_source": provider,
                "movie_id": movie.get("id"),
                "movie_title": movie_title,
                "movie_year": str(movie.get("release_date") or "")[:4],
            },
        })
        return TorrentItem.from_mapping(base)

    def fetch(self) -> Iterable[TorrentItem]:
        source_id = str(self.source.get("id") or "yts_meta_search")
        mode = str(self.source.get("mode") or "movie")
        quality = str(self.source.get("quality") or "all")
        refresh_days = max(1, int(self.source.get("refresh_days", 30)))
        refresh_pages = max(1, int(self.source.get("discovery_refresh_pages", 25)))
        state_path = self.source.get("_catalog_path")
        store = CatalogStore(Path(state_path)) if state_path else None
        checkpoint = store.get_checkpoint(source_id, "discover-next-page") if store else None
        page = 1 if checkpoint in (None, -1) else max(1, int(checkpoint))
        refresh_only = checkpoint == -1
        try:
            while True:
                discovery = self._payload({"api": "discover", "mode": mode, "page": page})
                movies = discovery.get("results") or []
                if not isinstance(movies, list) or not movies:
                    if store:
                        store.set_checkpoint(source_id, "discover-next-page", -1, self.fetched_at)
                    break
                for movie in movies:
                    if not isinstance(movie, dict):
                        continue
                    movie_id = str(movie.get("id") or "").strip()
                    key = f"movie:{movie_id}" if movie_id else f"movie-title:{slugify(movie.get('title'))}"
                    last_fetch = store.get_checkpoint(source_id, key) if store else None
                    if _fresh(last_fetch, refresh_days):
                        continue
                    movie_title = str(movie.get("title") or movie.get("original_title") or "").strip()
                    if not movie_title:
                        continue
                    payload = self._payload({
                        "api": "torrents",
                        "mode": mode,
                        "name": movie_title,
                        "year": str(movie.get("release_date") or "")[:4],
                        "quality": quality,
                    })
                    hits = payload.get("hits") or []
                    if isinstance(hits, list):
                        for hit in hits:
                            if isinstance(hit, dict):
                                item = self._item(movie, hit)
                                if item:
                                    yield item
                    if store:
                        store.set_checkpoint(source_id, key, self.fetched_at, self.fetched_at)
                page += 1
                if store:
                    store.set_checkpoint(source_id, "discover-next-page", page, self.fetched_at)
                total_pages = _number(discovery.get("total_pages"))
                if (total_pages and page > total_pages) or (refresh_only and page > refresh_pages):
                    if store:
                        store.set_checkpoint(source_id, "discover-next-page", -1, self.fetched_at)
                    break
        finally:
            if store:
                store.close()
