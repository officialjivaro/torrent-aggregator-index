from __future__ import annotations

import hashlib
import re
import unicodedata
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

from .models import TorrentItem

_SIZE_RE = re.compile(r"(?P<num>\d+(?:\.\d+)?)\s*(?P<unit>[KMGTPE]?i?B|[KMGTPE])\b", re.I)
_INFOHASH_RE = re.compile(r"^[a-fA-F0-9]{40}$|^[a-zA-Z2-7]{32}$")

TEXT_FIELDS = [
    "title",
    "category",
    "subcategory",
    "source_id",
    "source_name",
    "source_url",
    "source_homepage",
    "details_url",
    "download_page_url",
    "torrent_url",
    "magnet",
    "infohash",
    "size",
    "date_added",
    "date_published",
    "language",
    "license",
    "license_url",
    "copyright_status",
    "description",
    "hash_source",
    "fetched_at",
]


def scalar_text(value: Any, *, separator: str = " ") -> str:
    """Return a stable text representation for loose upstream metadata.

    Some sources, especially Internet Archive advancedsearch, can return fields
    such as title, description, licenseurl, date, or creator as lists. The rest
    of the backend expects text for most TorrentItem fields, so normalize these
    values in one place instead of letting list objects reach `.lower()` calls.
    """

    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, (int, float, bool)):
        return str(value).strip()
    if isinstance(value, dict):
        parts = [scalar_text(v, separator=separator) for v in value.values()]
        return separator.join(part for part in parts if part).strip()
    if isinstance(value, (list, tuple, set)):
        parts = [scalar_text(v, separator=separator) for v in value]
        return separator.join(part for part in parts if part).strip()
    return str(value).strip()


def first_scalar_text(value: Any) -> str:
    """Return the first non-empty scalar string from possibly nested metadata."""

    if value is None:
        return ""
    if isinstance(value, (str, int, float, bool)):
        return scalar_text(value)
    if isinstance(value, dict):
        for item in value.values():
            text = first_scalar_text(item)
            if text:
                return text
        return ""
    if isinstance(value, (list, tuple, set)):
        for item in value:
            text = first_scalar_text(item)
            if text:
                return text
        return ""
    return scalar_text(value)


def slugify(value: Any, fallback: str = "item") -> str:
    text = unicodedata.normalize("NFKD", scalar_text(value)).encode("ascii", "ignore").decode("ascii")
    text = re.sub(r"[^a-zA-Z0-9]+", "-", text).strip("-").lower()
    return text[:90] or fallback


def normalize_title(value: Any) -> str:
    text = scalar_text(value).lower()
    text = re.sub(r"\.torrent$", "", text)
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def parse_size_to_bytes(value: Any) -> int | None:
    if value is None or value == "":
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    text = first_scalar_text(value)
    if text.isdigit():
        return int(text)
    m = _SIZE_RE.search(text)
    if not m:
        return None
    num = float(m.group("num"))
    unit = m.group("unit").lower().replace("ib", "b")
    powers = {"b": 0, "k": 1, "kb": 1, "m": 2, "mb": 2, "g": 3, "gb": 3, "t": 4, "tb": 4, "p": 5, "pb": 5, "e": 6, "eb": 6}
    power = powers.get(unit)
    if power is None:
        return None
    return int(num * (1024 ** power))


def format_size(size_bytes: int | None) -> str | None:
    if size_bytes is None:
        return None
    value = float(size_bytes)
    for unit in ["B", "KiB", "MiB", "GiB", "TiB", "PiB"]:
        if value < 1024 or unit == "PiB":
            if unit == "B":
                return f"{int(value)} {unit}"
            return f"{value:.1f} {unit}"
        value /= 1024
    return str(size_bytes)


def extract_infohash_from_magnet(magnet: Any) -> str | None:
    magnet_text = first_scalar_text(magnet)
    if not magnet_text:
        return None
    try:
        parsed = urlparse(magnet_text)
        if parsed.scheme != "magnet":
            return None
        xt_values = parse_qs(parsed.query).get("xt", [])
        for xt in xt_values:
            xt = unquote(xt)
            if xt.startswith("urn:btih:"):
                candidate = xt.rsplit(":", 1)[-1]
                if _INFOHASH_RE.match(candidate):
                    return candidate.lower()
    except Exception:
        return None
    return None


def clean_tags(tags: Any) -> list[str]:
    if tags is None or tags == "":
        return []
    if isinstance(tags, str):
        raw_values: list[Any] = re.split(r"[,|]", tags)
    elif isinstance(tags, dict):
        raw_values = list(tags.values())
    elif isinstance(tags, (list, tuple, set)):
        raw_values = list(tags)
    else:
        raw_values = [tags]

    result: list[str] = []
    for tag in raw_values:
        if isinstance(tag, (list, tuple, set)):
            for nested in clean_tags(tag):
                if nested and nested not in result:
                    result.append(nested)
            continue
        value = slugify(tag, fallback="")
        if value and value not in result:
            result.append(value)
    return result


def stable_id(*parts: Any) -> str:
    key = "|".join([scalar_text(p) for p in parts])
    return hashlib.sha1(key.encode("utf-8")).hexdigest()[:16]


def _normalize_text_fields(item: TorrentItem) -> None:
    for field_name in TEXT_FIELDS:
        value = getattr(item, field_name)
        if value is None:
            continue
        text = scalar_text(value)
        setattr(item, field_name, text or None)


def normalize_item(item: TorrentItem | dict[str, Any]) -> TorrentItem:
    if isinstance(item, dict):
        item = TorrentItem.from_mapping(item)

    _normalize_text_fields(item)

    if item.magnet and not item.infohash:
        ih = extract_infohash_from_magnet(item.magnet)
        if ih:
            item.infohash = ih
            item.hash_source = item.hash_source or "magnet"

    item.normalized_title = normalize_title(item.title)
    if item.size_bytes is None and item.size:
        item.size_bytes = parse_size_to_bytes(item.size)
    if item.size is None and item.size_bytes is not None:
        item.size = format_size(item.size_bytes)

    item.tags = clean_tags(item.tags)

    if not item.slug:
        basis = item.title or item.infohash or item.torrent_url or item.source_url or "item"
        item.slug = slugify(basis)
    if not item.id:
        basis = item.infohash or item.magnet or item.torrent_url or item.details_url or item.source_url or item.title
        item.id = f"{slugify(item.source_id or item.source_name or 'src')}-{stable_id(basis, item.title, item.size)}"

    if not item.source_url:
        item.source_url = item.details_url or item.download_page_url or item.source_homepage
    if not item.details_url:
        item.details_url = item.source_url or item.source_homepage
    return item
