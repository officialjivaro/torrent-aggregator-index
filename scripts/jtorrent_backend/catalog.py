from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import urlparse, urlunparse

import yaml

from .dedupe import dedupe_key, merge_items
from .models import TorrentItem
from .normalize import normalize_item


def _normal_url(value: str | None) -> str | None:
    if not value:
        return None
    parsed = urlparse(str(value).strip())
    return urlunparse((parsed.scheme.lower(), parsed.netloc.lower(), parsed.path.rstrip("/"), "", parsed.query, ""))


@dataclass(frozen=True)
class Suppressions:
    infohashes: frozenset[str]
    urls: frozenset[str]

    def matches(self, item: TorrentItem) -> bool:
        if item.infohash and item.infohash.lower() in self.infohashes:
            return True
        values = [item.source_url, item.details_url, item.download_page_url, item.torrent_url]
        values.extend(str(mirror.get(key) or "") for mirror in item.mirrors for key in ("source_url", "details_url", "torrent_url"))
        return any(normalized in self.urls for normalized in (_normal_url(value) for value in values) if normalized)


EMPTY_SUPPRESSIONS = Suppressions(frozenset(), frozenset())


class CatalogCapacityError(RuntimeError):
    pass


def load_suppressions(path: Path | None) -> Suppressions:
    if path is None or not path.is_file():
        return EMPTY_SUPPRESSIONS
    payload = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(payload, dict):
        raise ValueError(f"Suppression registry must be an object: {path}")
    infohashes = frozenset(str(value).strip().lower() for value in payload.get("infohashes", []) if str(value).strip())
    urls = frozenset(value for value in (_normal_url(str(raw)) for raw in payload.get("urls", [])) if value)
    return Suppressions(infohashes, urls)


def item_source_ids(item: TorrentItem) -> set[str]:
    values = {str(item.source_id)} if item.source_id else set()
    values.update(str(mirror.get("source_id")) for mirror in item.mirrors if mirror.get("source_id"))
    return values


class CatalogStore:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(self.path)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA journal_mode=WAL")
        self.connection.execute("PRAGMA synchronous=NORMAL")
        self._initialize()

    def _initialize(self) -> None:
        self.connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS records (
                dedupe_key TEXT PRIMARY KEY,
                payload_json TEXT NOT NULL,
                first_seen TEXT NOT NULL,
                last_seen TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS checkpoints (
                source_id TEXT NOT NULL,
                checkpoint_key TEXT NOT NULL,
                value_json TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                PRIMARY KEY (source_id, checkpoint_key)
            );
            CREATE TABLE IF NOT EXISTS collection_runs (
                run_id INTEGER PRIMARY KEY AUTOINCREMENT,
                generated_at TEXT NOT NULL,
                fetched_count INTEGER NOT NULL,
                catalog_count INTEGER NOT NULL,
                suppressed_count INTEGER NOT NULL
            );
            """
        )
        self.connection.commit()

    def close(self) -> None:
        self.connection.close()

    def __enter__(self) -> "CatalogStore":
        return self

    def __exit__(self, _exc_type, _exc, _traceback) -> None:
        self.close()

    def merge(self, items: Iterable[TorrentItem], seen_at: str, soft_limit_bytes: int | None = None) -> int:
        if soft_limit_bytes and self.size_bytes() >= soft_limit_bytes:
            raise CatalogCapacityError(
                f"Catalog is already at its configured soft limit of {soft_limit_bytes} bytes: {self.path}"
            )
        count = 0
        try:
            with self.connection:
                for raw in items:
                    incoming = normalize_item(raw)
                    incoming.first_seen = incoming.first_seen or seen_at
                    incoming.last_seen = seen_at
                    key = dedupe_key(incoming)
                    row = self.connection.execute(
                        "SELECT payload_json, first_seen, last_seen FROM records WHERE dedupe_key = ?", (key,)
                    ).fetchone()
                    if row:
                        existing = normalize_item(TorrentItem.from_mapping(json.loads(row["payload_json"])))
                        existing.first_seen = row["first_seen"]
                        existing.last_seen = row["last_seen"]
                        merged = merge_items(existing, incoming)
                    else:
                        merged = incoming
                    first_seen = merged.first_seen or seen_at
                    last_seen = max(str(merged.last_seen or seen_at), seen_at)
                    merged.first_seen = first_seen
                    merged.last_seen = last_seen
                    self.connection.execute(
                        """
                        INSERT INTO records (dedupe_key, payload_json, first_seen, last_seen)
                        VALUES (?, ?, ?, ?)
                        ON CONFLICT(dedupe_key) DO UPDATE SET
                            payload_json = excluded.payload_json,
                            first_seen = excluded.first_seen,
                            last_seen = excluded.last_seen
                        """,
                        (key, json.dumps(merged.to_dict(), ensure_ascii=False, separators=(",", ":")), first_seen, last_seen),
                    )
                    count += 1
                    if soft_limit_bytes and count % 100 == 0 and self.size_bytes() > soft_limit_bytes:
                        raise CatalogCapacityError(
                            f"Catalog batch would exceed its configured soft limit of {soft_limit_bytes} bytes: {self.path}"
                        )
                if soft_limit_bytes and self.size_bytes() > soft_limit_bytes:
                    raise CatalogCapacityError(
                        f"Catalog batch would exceed its configured soft limit of {soft_limit_bytes} bytes: {self.path}"
                    )
        except CatalogCapacityError:
            self.connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            raise
        return count

    def load_active(self, source_ids: set[str], suppressions: Suppressions = EMPTY_SUPPRESSIONS) -> tuple[list[TorrentItem], int]:
        items: list[TorrentItem] = []
        suppressed = 0
        for row in self.connection.execute("SELECT payload_json, first_seen, last_seen FROM records ORDER BY dedupe_key"):
            item = normalize_item(TorrentItem.from_mapping(json.loads(row["payload_json"])))
            item.first_seen = row["first_seen"]
            item.last_seen = row["last_seen"]
            if not item_source_ids(item).intersection(source_ids):
                continue
            if suppressions.matches(item):
                suppressed += 1
                continue
            items.append(item)
        return items, suppressed

    def get_checkpoint(self, source_id: str, key: str, default: Any = None) -> Any:
        row = self.connection.execute(
            "SELECT value_json FROM checkpoints WHERE source_id = ? AND checkpoint_key = ?", (source_id, key)
        ).fetchone()
        return json.loads(row["value_json"]) if row else default

    def set_checkpoint(self, source_id: str, key: str, value: Any, updated_at: str) -> None:
        with self.connection:
            self.connection.execute(
                """
                INSERT INTO checkpoints (source_id, checkpoint_key, value_json, updated_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(source_id, checkpoint_key) DO UPDATE SET
                    value_json = excluded.value_json,
                    updated_at = excluded.updated_at
                """,
                (source_id, key, json.dumps(value, separators=(",", ":")), updated_at),
            )

    def record_run(self, generated_at: str, fetched_count: int, catalog_count: int, suppressed_count: int) -> None:
        with self.connection:
            self.connection.execute(
                "INSERT INTO collection_runs (generated_at, fetched_count, catalog_count, suppressed_count) VALUES (?, ?, ?, ?)",
                (generated_at, fetched_count, catalog_count, suppressed_count),
            )

    def count(self) -> int:
        return int(self.connection.execute("SELECT COUNT(*) FROM records").fetchone()[0])

    def size_bytes(self) -> int:
        return sum(
            candidate.stat().st_size
            for candidate in (self.path, Path(f"{self.path}-wal"), Path(f"{self.path}-shm"))
            if candidate.exists()
        )
