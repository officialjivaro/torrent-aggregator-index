from __future__ import annotations

import math
from typing import Any, Iterable
from urllib.parse import urlencode

from ..models import TorrentItem
from ..normalize import first_scalar_text, scalar_text
from ..timeutil import to_date
from .base import SourceAdapter


class InternetArchiveAdvancedSearchSource(SourceAdapter):
    API = "https://archive.org/advancedsearch.php"

    def _int(self, key: str, default: int) -> int:
        return _as_int(self.source.get(key), default)

    def _sorts(self) -> list[str]:
        raw = self.source.get("sorts", self.source.get("sort"))
        if raw is None:
            return []
        if isinstance(raw, str):
            return [raw]
        if isinstance(raw, (list, tuple, set)):
            return [scalar_text(item) for item in raw if scalar_text(item)]
        return [scalar_text(raw)] if scalar_text(raw) else []

    def _url(self, *, query: str, rows: int, page: int, fields: list[str]) -> str:
        params: list[tuple[str, str | int]] = [
            ("q", query),
            ("rows", rows),
            ("page", page),
            ("output", "json"),
        ]
        for sort in self._sorts():
            params.append(("sort[]", sort))
        for field in fields:
            params.append(("fl[]", field))
        return f"{self.API}?{urlencode(params)}"

    def _item_from_doc(self, doc: dict[str, Any], *, include_torrent: bool) -> TorrentItem | None:
        identifier = first_scalar_text(doc.get("identifier"))
        if not identifier:
            return None

        details_url = f"https://archive.org/details/{identifier}"
        torrent_url = f"https://archive.org/download/{identifier}/{identifier}_archive.torrent" if include_torrent else None
        title = scalar_text(doc.get("title")) or identifier.replace("_", " ")
        description = scalar_text(doc.get("description"))
        license_url = first_scalar_text(doc.get("licenseurl")) or self.source.get("license_url")
        item_size = _as_int(doc.get("item_size"), 0)
        downloads = _as_int(doc.get("downloads"), 0)

        item_data = self.base_item()
        item_data.update(
            {
                "title": title,
                "source_url": details_url,
                "details_url": details_url,
                "download_page_url": details_url,
                "torrent_url": torrent_url,
                "date_added": to_date(doc.get("publicdate")),
                "date_published": to_date(doc.get("date")),
                "license_url": license_url,
                "description": description or None,
                "size_bytes": item_size or None,
                "completed": downloads or None,
                "raw": {"internet_archive": doc},
            }
        )
        return TorrentItem.from_mapping(item_data)

    def fetch(self) -> Iterable[TorrentItem]:
        query = self.source.get("query")
        if not query:
            return

        # archive.org supports paginated advancedsearch JSON. The original
        # adapter requested page=1 only, so every Archive source was hard-capped
        # at one page even when the source query contained many more records.
        rows = max(1, min(self._int("rows", 250), 10000))
        max_items = max(0, self._int("max_items", rows))
        configured_max_pages = self.source.get("max_pages")
        if configured_max_pages is None:
            max_pages = max(1, math.ceil(max_items / rows)) if max_items else 1
        else:
            max_pages = max(1, self._int("max_pages", 1))
        start_page = max(1, self._int("start_page", 1))
        include_torrent = bool(self.source.get("include_torrent_url", False))
        fields = [
            "identifier",
            "title",
            "description",
            "creator",
            "mediatype",
            "date",
            "publicdate",
            "licenseurl",
            "downloads",
            "item_size",
        ]
        produced = 0

        for page in range(start_page, start_page + max_pages):
            url = self._url(query=scalar_text(query), rows=rows, page=page, fields=fields)
            data = self.http.get(url).json()
            docs = data.get("response", {}).get("docs", [])
            if not isinstance(docs, list) or not docs:
                break

            for doc in docs:
                if not isinstance(doc, dict):
                    continue
                item = self._item_from_doc(doc, include_torrent=include_torrent)
                if item is None:
                    continue
                yield item
                produced += 1
                if max_items and produced >= max_items:
                    return

            if len(docs) < rows:
                break


def _as_int(value: Any, default: int) -> int:
    if value is None or value == "":
        return default
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, dict):
        return _as_int(first_scalar_text(value), default)
    if isinstance(value, (list, tuple, set)):
        return _as_int(first_scalar_text(value), default)
    text = str(value).strip().replace(",", "")
    if not text:
        return default
    try:
        return int(float(text))
    except (TypeError, ValueError):
        return default
