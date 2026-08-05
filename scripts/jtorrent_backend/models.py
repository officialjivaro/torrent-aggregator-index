from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Any


@dataclass
class TorrentItem:
    id: str | None = None
    slug: str | None = None
    title: str | None = None
    normalized_title: str | None = None
    category: str | None = None
    subcategory: str | None = None
    source_id: str | None = None
    source_name: str | None = None
    source_url: str | None = None
    source_homepage: str | None = None
    details_url: str | None = None
    download_page_url: str | None = None
    torrent_url: str | None = None
    magnet: str | None = None
    infohash: str | None = None
    trackers: list[str] = field(default_factory=list)
    size_bytes: int | None = None
    size: str | None = None
    seeders: int | None = None
    leechers: int | None = None
    completed: int | None = None
    date_added: str | None = None
    date_published: str | None = None
    language: str | None = None
    license: str | None = None
    license_url: str | None = None
    copyright_status: str | None = None
    description: str | None = None
    tags: list[str] = field(default_factory=list)
    files: list[dict[str, Any]] = field(default_factory=list)
    mirrors: list[dict[str, Any]] = field(default_factory=list)
    hash_source: str | None = None
    fetched_at: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_mapping(cls, data: dict[str, Any]) -> "TorrentItem":
        allowed = set(cls.__dataclass_fields__.keys())
        item = cls(**{k: v for k, v in data.items() if k in allowed})
        extra = {k: v for k, v in data.items() if k not in allowed}
        if extra:
            item.raw.update(extra)
        return item
