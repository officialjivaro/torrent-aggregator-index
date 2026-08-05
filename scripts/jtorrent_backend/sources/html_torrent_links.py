from __future__ import annotations

import re
from collections import deque
from typing import Iterable
from urllib.parse import urldefrag, urljoin, urlparse

from ..models import TorrentItem
from ..normalize import parse_size_to_bytes
from ..timeutil import to_date
from ..torrent_metadata import parse_torrent_bytes
from .base import SourceAdapter

_DATE_PATTERNS = [
    re.compile(r"\b\d{4}-[A-Za-z]{3}-\d{2}\s+\d{2}:\d{2}\b"),
    re.compile(r"\b\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}\b"),
    re.compile(r"\b\d{4}-\d{2}-\d{2}\b"),
]
_SIZE_PATTERN = re.compile(r"\b\d+(?:\.\d+)?\s*(?:KiB|MiB|GiB|TiB|KB|MB|GB|TB|K|M|G|T)\b", re.I)
_MAGNET_RE = re.compile(r"magnet:\?[^\s\"'<>]+", re.I)
_TORRENT_URL_RE = re.compile(r"https?://[^\s\"'<>]+?\.torrent(?:[?#][^\s\"'<>]*)?", re.I)
_URL_ATTRIBUTES = ["href", "data-href", "data-url", "data-link", "data-download", "data-magnet", "value"]



def _expand_url_templates(source: dict) -> list[str]:
    urls = [str(url) for url in source.get("page_urls") or []]
    templates = source.get("page_url_templates") or []
    for entry in templates:
        if isinstance(entry, str):
            template = entry
            start = int(source.get("start_page", 1))
            stop = int(source.get("max_pages", 1))
            end = start + max(0, stop) - 1
        else:
            template = str(entry.get("template") or entry.get("url") or "")
            start = int(entry.get("start", entry.get("start_page", 1)))
            if entry.get("end") is not None:
                end = int(entry.get("end"))
            else:
                count = int(entry.get("count", entry.get("max_pages", source.get("max_pages", 1))))
                end = start + max(0, count) - 1
        if not template:
            continue
        for page in range(start, end + 1):
            urls.append(template.format(page=page, n=page))
    # Preserve order while de-duping.
    seen: set[str] = set()
    out: list[str] = []
    for url in urls:
        if url not in seen:
            seen.add(url)
            out.append(url)
    return out


def _compile_many(patterns: list[str] | None) -> list[re.Pattern]:
    return [re.compile(p, re.I) for p in patterns or []]


def _matches_any(url: str, patterns: list[re.Pattern], default: bool = True) -> bool:
    if not patterns:
        return default
    return any(p.search(url) for p in patterns)


def _title_from_url(url: str) -> str:
    if url.startswith("magnet:?"):
        return "Magnet link"
    path = urlparse(url).path.rstrip("/")
    name = path.rsplit("/", 1)[-1] or url
    name = re.sub(r"\.torrent(?:[?#].*)?$", "", name, flags=re.I)
    name = name.replace("_", " ").replace("-", " ")
    return re.sub(r"\s+", " ", name).strip() or url


def _row_text(anchor) -> str:
    if anchor is None:
        return ""
    for parent_name in ["tr", "li", "p", "article", "section", "div"]:
        parent = anchor.find_parent(parent_name)
        if parent:
            return " ".join(parent.get_text(" ", strip=True).split())
    return " ".join(anchor.get_text(" ", strip=True).split())


def _extract_date(text: str) -> str | None:
    for pattern in _DATE_PATTERNS:
        m = pattern.search(text)
        if m:
            return to_date(m.group(0))
    return None


def _extract_size(text: str) -> tuple[int | None, str | None]:
    m = _SIZE_PATTERN.search(text)
    if not m:
        return None, None
    value = m.group(0)
    return parse_size_to_bytes(value), value


def _normalize_candidate(page_url: str, value: str) -> str:
    value = str(value or "").strip()
    if not value:
        return ""
    if value.startswith("magnet:?"):
        return value
    return urldefrag(urljoin(page_url, value))[0]


class HtmlTorrentLinksSource(SourceAdapter):
    def _candidate_values_from_tag(self, tag) -> list[str]:
        values: list[str] = []
        for attr in _URL_ATTRIBUTES:
            raw = tag.get(attr)
            if not raw:
                continue
            if isinstance(raw, list):
                values.extend(str(item) for item in raw)
            else:
                values.append(str(raw))
        return values

    def _make_item(self, *, page_url: str, absolute: str, anchor=None) -> TorrentItem:
        text = _row_text(anchor) if anchor is not None else ""
        size_bytes, size_text = _extract_size(text)
        date = _extract_date(text)
        title = ""
        if anchor is not None:
            title = (
                anchor.get_text(" ", strip=True)
                or str(anchor.get("title") or "").strip()
                or str(anchor.get("aria-label") or "").strip()
            )
        if not title or title.lower() in {"torrent", "torrent download", "64-bit", "download", "magnet"}:
            title = _title_from_url(absolute)

        is_magnet = absolute.startswith("magnet:?")
        data = self.base_item()
        data.update(
            {
                "title": title,
                "source_url": page_url,
                "details_url": page_url,
                "download_page_url": page_url,
                "torrent_url": None if is_magnet else absolute,
                "magnet": absolute if is_magnet else None,
                "size_bytes": size_bytes,
                "size": size_text,
                "date_added": date,
                "description": text if text and text != title else self.source.get("license"),
            }
        )
        return TorrentItem.from_mapping(data)

    def fetch(self) -> Iterable[TorrentItem]:
        include_url = _compile_many(self.source.get("include_url_regexes") or [r"\.torrent(?:$|[?#])"])
        exclude_url = _compile_many(self.source.get("exclude_url_regexes"))
        follow_link = _compile_many(self.source.get("follow_link_regexes"))
        max_depth = int(self.source.get("max_depth", 0))
        max_items = int(self.source.get("max_items", 1000))
        collect_magnets = bool(self.source.get("collect_magnets", False))
        fetch_torrent_metadata = bool(self.source.get("fetch_torrent_metadata", False))
        same_host_only = self.source.get("same_host_only", True) is not False

        queue: deque[tuple[str, int]] = deque((url, 0) for url in _expand_url_templates(self.source))
        seen_pages: set[str] = set()
        seen_links: set[str] = set()
        produced = 0

        def maybe_yield_candidate(page_url: str, absolute: str, anchor=None) -> TorrentItem | None:
            nonlocal produced
            if produced >= max_items:
                return None
            if not absolute:
                return None
            is_magnet = absolute.startswith("magnet:?")
            is_candidate = is_magnet if collect_magnets else False
            if _matches_any(absolute, include_url, default=False):
                is_candidate = True
            if _matches_any(absolute, exclude_url, default=False):
                is_candidate = False
            if not is_candidate or absolute in seen_links:
                return None
            seen_links.add(absolute)
            item = self._make_item(page_url=page_url, absolute=absolute, anchor=anchor)

            if fetch_torrent_metadata and item.torrent_url:
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

            produced += 1
            return item

        while queue and produced < max_items:
            page_url, depth = queue.popleft()
            page_url = urldefrag(page_url)[0]
            if page_url in seen_pages:
                continue
            seen_pages.add(page_url)

            try:
                from bs4 import BeautifulSoup
            except ImportError as exc:
                raise RuntimeError(
                    "beautifulsoup4 is required for html_torrent_links sources; run `python -m pip install -r requirements.txt`"
                ) from exc
            html = self.http.get_text(page_url)
            soup = BeautifulSoup(html, "html.parser")

            for tag in soup.find_all(True):
                for raw_value in self._candidate_values_from_tag(tag):
                    absolute = _normalize_candidate(page_url, raw_value)
                    if not absolute:
                        continue

                    # Follow configured in-domain pages first; candidates on those
                    # detail pages are then extracted in a later queue iteration.
                    if depth < max_depth and _matches_any(absolute, follow_link, default=False):
                        if not same_host_only or urlparse(absolute).hostname == urlparse(page_url).hostname:
                            queue.append((absolute, depth + 1))

                    item = maybe_yield_candidate(page_url, absolute, tag)
                    if item is not None:
                        yield item
                        if produced >= max_items:
                            break
                if produced >= max_items:
                    break

            if produced >= max_items:
                break

            # Some sites put magnet or .torrent URLs inside scripts or data blobs
            # instead of anchor hrefs. Regex scanning the raw HTML catches those
            # without requiring a site-specific parser.
            raw_candidates = []
            if collect_magnets:
                raw_candidates.extend(m.group(0) for m in _MAGNET_RE.finditer(html))
            raw_candidates.extend(m.group(0) for m in _TORRENT_URL_RE.finditer(html))
            for raw_value in raw_candidates:
                item = maybe_yield_candidate(page_url, _normalize_candidate(page_url, raw_value), None)
                if item is not None:
                    yield item
                    if produced >= max_items:
                        break
