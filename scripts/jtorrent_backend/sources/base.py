from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Iterable

from ..http import HttpClient
from ..models import TorrentItem
from ..timeutil import utc_now_iso


class SourceAdapter(ABC):
    def __init__(self, source: dict, http: HttpClient):
        self.source = source
        self.http = http
        self.fetched_at = utc_now_iso()

    def base_item(self) -> dict:
        return {
            "category": self.source.get("category"),
            "source_id": self.source.get("id"),
            "source_name": self.source.get("name"),
            "source_homepage": self.source.get("source_homepage"),
            "source_url": self.source.get("source_homepage"),
            "license": self.source.get("license"),
            "license_url": self.source.get("license_url"),
            "copyright_status": self.source.get("copyright_status"),
            "tags": list(self.source.get("tags") or []),
            "fetched_at": self.fetched_at,
        }

    @abstractmethod
    def fetch(self) -> Iterable[TorrentItem]:
        raise NotImplementedError
