from __future__ import annotations

from typing import Iterable

from ..models import TorrentItem
from ..torrent_metadata import parse_torrent_bytes
from .base import SourceAdapter


class DirectListSource(SourceAdapter):
    def fetch(self) -> Iterable[TorrentItem]:
        for raw in self.source.get("items") or []:
            data = self.base_item()
            data.update(raw)
            item = TorrentItem.from_mapping(data)
            if self.source.get("fetch_torrent_metadata") and item.torrent_url:
                try:
                    meta = parse_torrent_bytes(self.http.get_limited_bytes(item.torrent_url))
                    item.infohash = item.infohash or meta.get("infohash")
                    item.hash_source = item.hash_source or "torrent"
                    item.trackers = item.trackers or meta.get("trackers") or []
                    item.files = item.files or meta.get("files") or []
                    item.size_bytes = item.size_bytes or meta.get("size_bytes")
                    item.title = item.title or meta.get("name")
                except Exception as exc:  # noqa: BLE001
                    item.raw["torrent_metadata_error"] = str(exc)
            yield item
