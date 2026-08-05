from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from types import SimpleNamespace
from typing import Iterable

from ..models import TorrentItem
from ..timeutil import to_date
from .base import SourceAdapter

_MAGNET_RE = re.compile(r"magnet:\?[^\s\"'<>]+", re.I)
_TORRENT_RE = re.compile(r"https?://[^\s\"'<>]+\.torrent(?:\?[^\s\"'<>]*)?", re.I)


def _expand_feed_urls(source: dict) -> list[str]:
    urls = [str(url) for url in source.get("feed_urls") or []]
    templates = source.get("feed_url_templates") or []
    for entry in templates:
        if isinstance(entry, str):
            template = entry
            start = int(source.get("start_page", 1))
            count = int(source.get("max_pages", 1))
            end = start + max(0, count) - 1
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
    seen: set[str] = set()
    out: list[str] = []
    for url in urls:
        if url not in seen:
            seen.add(url)
            out.append(url)
    return out


def _strip_namespace(tag: str) -> str:
    return tag.rsplit("}", 1)[-1] if "}" in tag else tag


def _child_text(node: ET.Element, names: set[str]) -> str | None:
    for child in list(node):
        if _strip_namespace(child.tag).lower() in names and child.text:
            return child.text.strip()
    return None


def _fallback_parse_entries(text: str):
    root = ET.fromstring(text)
    entries = []
    for node in root.iter():
        tag = _strip_namespace(node.tag).lower()
        if tag not in {"item", "entry"}:
            continue
        links: list[dict[str, str]] = []
        link_text = _child_text(node, {"link"})
        if link_text:
            links.append({"href": link_text})
        for child in list(node):
            ctag = _strip_namespace(child.tag).lower()
            if ctag in {"enclosure", "link"}:
                href = child.attrib.get("url") or child.attrib.get("href")
                if href:
                    links.append({"href": href, "type": child.attrib.get("type", "")})
        entries.append(
            SimpleNamespace(
                title=_child_text(node, {"title"}),
                link=link_text,
                summary=_child_text(node, {"summary", "description", "content"}),
                description=_child_text(node, {"description"}),
                published=_child_text(node, {"published", "pubdate", "date"}),
                updated=_child_text(node, {"updated"}),
                links=links,
            )
        )
    return entries


def _parse_entries(text: str):
    try:
        import feedparser  # type: ignore
    except ImportError:
        return _fallback_parse_entries(text)
    parsed = feedparser.parse(text)
    return parsed.entries


class RssFeedSource(SourceAdapter):
    def fetch(self) -> Iterable[TorrentItem]:
        for feed_url in _expand_feed_urls(self.source):
            text = self.http.get_text(feed_url)
            for entry in _parse_entries(text):
                raw_text = " ".join(str(getattr(entry, key, "") or "") for key in ["summary", "description", "content", "links"])
                magnet = None
                torrent_url = None
                m = _MAGNET_RE.search(raw_text)
                if m:
                    magnet = m.group(0)
                t = _TORRENT_RE.search(raw_text)
                if t:
                    torrent_url = t.group(0)
                for link in getattr(entry, "links", []) or []:
                    href = link.get("href") if isinstance(link, dict) else getattr(link, "href", None)
                    ltype = str((link.get("type") if isinstance(link, dict) else getattr(link, "type", "")) or "").lower()
                    if href and (str(href).endswith(".torrent") or "bittorrent" in ltype):
                        torrent_url = href
                    if href and str(href).startswith("magnet:?"):
                        magnet = href

                data = self.base_item()
                entry_link = getattr(entry, "link", None) or feed_url
                data.update(
                    {
                        "title": getattr(entry, "title", None),
                        "source_url": entry_link,
                        "details_url": entry_link,
                        "download_page_url": entry_link,
                        "torrent_url": torrent_url,
                        "magnet": magnet,
                        "date_published": to_date(getattr(entry, "published", None) or getattr(entry, "updated", None)),
                        "description": getattr(entry, "summary", None),
                        "raw": {"feed_url": feed_url},
                    }
                )
                yield TorrentItem.from_mapping(data)
