from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import quote, urlencode

from ..catalog import CatalogStore
from ..models import TorrentItem
from ..normalize import clean_tags
from .base import SourceAdapter


CATEGORY_NAMES = {
    "100": "audio",
    "200": "movies",
    "300": "software",
    "400": "games",
    "500": "general",
    "600": "general",
}


def _number(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


class ApiBaySource(SourceAdapter):
    def _payload(self, url: str, params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        if params:
            url = f"{url}?{urlencode(params)}"
        payload = self.http.get(url).json()
        if not isinstance(payload, list):
            raise RuntimeError(f"APIBay returned a non-list response for {url}")
        return [item for item in payload if isinstance(item, dict) and str(item.get("id") or "0") != "0"]

    def _item(self, row: dict[str, Any]) -> TorrentItem | None:
        infohash = str(row.get("info_hash") or "").strip().lower()
        title = str(row.get("name") or "").strip()
        if not title or not infohash:
            return None
        raw_category = str(row.get("category") or "")
        broad_category = CATEGORY_NAMES.get(raw_category[:1] + "00", self.source.get("category", "general"))
        added = _number(row.get("added"))
        added_iso = datetime.fromtimestamp(added, tz=timezone.utc).isoformat().replace("+00:00", "Z") if added else None
        torrent_id = str(row.get("id") or "").strip()
        username = str(row.get("username") or "").strip()
        tags = [*self.source.get("tags", []), broad_category]
        if username:
            tags.append(username)
        base = self.base_item()
        base.update({
            "title": title,
            "category": broad_category,
            "details_url": f"https://thepiratebay.org/description.php?id={quote(torrent_id)}" if torrent_id else self.source.get("source_homepage"),
            "source_url": f"https://thepiratebay.org/description.php?id={quote(torrent_id)}" if torrent_id else self.source.get("source_homepage"),
            "magnet": f"magnet:?xt=urn:btih:{infohash}&dn={quote(title)}",
            "infohash": infohash,
            "hash_source": "apibay",
            "size_bytes": _number(row.get("size")),
            "seeders": _number(row.get("seeders")),
            "leechers": _number(row.get("leechers")),
            "date_added": added_iso,
            "tags": clean_tags(tags),
            "raw": {
                "apibay_id": torrent_id,
                "uploader": username,
                "category_id": raw_category,
                "uploader_status": row.get("status"),
            },
        })
        return TorrentItem.from_mapping(base)

    def fetch(self) -> Iterable[TorrentItem]:
        api_base = str(self.source.get("api_base_url") or "https://apibay.org/").rstrip("/")
        seen_hashes: set[str] = set()
        uploaders: set[str] = set()

        def emit(rows: list[dict[str, Any]]) -> Iterable[TorrentItem]:
            for row in rows:
                item = self._item(row)
                if not item or not item.infohash or item.infohash in seen_hashes:
                    continue
                seen_hashes.add(item.infohash)
                uploader = str(item.raw.get("uploader") or "").strip()
                if uploader and uploader.lower() not in {"anonymous", "anon"}:
                    uploaders.add(uploader)
                yield item

        for category in self.source.get("top_categories") or ["recent", "all"]:
            url = f"{api_base}/precompiled/data_top100_{category}.json"
            yield from emit(self._payload(url))

        if self.source.get("expand_uploaders") is not True or not uploaders:
            return

        state_path = self.source.get("_catalog_path")
        store = CatalogStore(Path(state_path)) if state_path else None
        try:
            refresh_pages = max(1, int(self.source.get("uploader_refresh_pages", 3)))
            for uploader in sorted(uploaders, key=str.lower):
                key = f"uploader:{uploader}"
                checkpoint = store.get_checkpoint(self.source["id"], key) if store else None
                page = 0 if checkpoint in (None, -1) else max(0, int(checkpoint))
                refresh_only = checkpoint == -1
                page_signatures: set[tuple[str, ...]] = set()
                while True:
                    rows = self._payload(f"{api_base}/q.php", {"q": f"user:{uploader}:{page}"})
                    signature = tuple(str(row.get("id") or "") for row in rows)
                    if not rows or signature in page_signatures:
                        if store:
                            store.set_checkpoint(self.source["id"], key, -1, self.fetched_at)
                        break
                    page_signatures.add(signature)
                    yield from emit(rows)
                    page += 1
                    if store:
                        store.set_checkpoint(self.source["id"], key, page, self.fetched_at)
                    if len(rows) < 50 or (refresh_only and page >= refresh_pages):
                        if store:
                            store.set_checkpoint(self.source["id"], key, -1, self.fetched_at)
                        break
        finally:
            if store:
                store.close()
