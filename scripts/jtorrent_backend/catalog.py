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


@dataclass(frozen=True)
class SnapshotMergeStats:
    processed: int
    inserted: int
    updated: int
    provenance_inserted: int


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
        self.connection.execute("PRAGMA foreign_keys=ON")
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
            CREATE TABLE IF NOT EXISTS historical_snapshots (
                snapshot_id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                source_id TEXT NOT NULL,
                snapshot_date TEXT NOT NULL,
                provenance_url TEXT NOT NULL,
                archive_url TEXT NOT NULL,
                checksum_algorithm TEXT NOT NULL,
                checksum_value TEXT NOT NULL,
                archive_bytes INTEGER NOT NULL,
                status TEXT NOT NULL,
                rows_seen INTEGER NOT NULL DEFAULT 0,
                inserted_count INTEGER NOT NULL DEFAULT 0,
                updated_count INTEGER NOT NULL DEFAULT 0,
                skipped_count INTEGER NOT NULL DEFAULT 0,
                started_at TEXT,
                completed_at TEXT,
                error TEXT
            );
            CREATE TABLE IF NOT EXISTS historical_snapshot_chunks (
                snapshot_id TEXT NOT NULL,
                chunk_name TEXT NOT NULL,
                status TEXT NOT NULL,
                rows_seen INTEGER NOT NULL DEFAULT 0,
                inserted_count INTEGER NOT NULL DEFAULT 0,
                updated_count INTEGER NOT NULL DEFAULT 0,
                skipped_count INTEGER NOT NULL DEFAULT 0,
                completed_at TEXT,
                PRIMARY KEY (snapshot_id, chunk_name),
                FOREIGN KEY (snapshot_id) REFERENCES historical_snapshots(snapshot_id)
            );
            CREATE TABLE IF NOT EXISTS record_provenance (
                dedupe_key TEXT NOT NULL,
                snapshot_id TEXT NOT NULL,
                source_record_id TEXT NOT NULL,
                observed_at TEXT NOT NULL,
                metadata_json TEXT NOT NULL,
                PRIMARY KEY (dedupe_key, snapshot_id, source_record_id),
                FOREIGN KEY (snapshot_id) REFERENCES historical_snapshots(snapshot_id)
            );
            CREATE INDEX IF NOT EXISTS record_provenance_snapshot_idx
                ON record_provenance(snapshot_id);
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

    def merge_snapshot(
        self,
        items: Iterable[TorrentItem],
        *,
        snapshot_id: str,
        observed_at: str,
        soft_limit_bytes: int | None = None,
    ) -> SnapshotMergeStats:
        """Merge one historical batch and attach durable, idempotent provenance."""

        if soft_limit_bytes and self.size_bytes() >= soft_limit_bytes:
            raise CatalogCapacityError(
                f"Catalog is already at its configured soft limit of {soft_limit_bytes} bytes: {self.path}"
            )
        processed = inserted = updated = provenance_inserted = 0
        try:
            with self.connection:
                for raw in items:
                    incoming = normalize_item(raw)
                    incoming.first_seen = incoming.first_seen or observed_at
                    incoming.last_seen = incoming.last_seen or observed_at
                    key = dedupe_key(incoming)
                    row = self.connection.execute(
                        "SELECT payload_json, first_seen, last_seen FROM records WHERE dedupe_key = ?", (key,)
                    ).fetchone()
                    if row:
                        existing = normalize_item(TorrentItem.from_mapping(json.loads(row["payload_json"])))
                        existing.first_seen = row["first_seen"]
                        existing.last_seen = row["last_seen"]
                        merged = merge_items(existing, incoming)
                        updated += 1
                    else:
                        merged = incoming
                        inserted += 1
                    first_seen = min(str(merged.first_seen or observed_at), observed_at)
                    last_seen = max(str(merged.last_seen or observed_at), observed_at)
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
                    source_record_id = str(
                        incoming.raw.get("archive_record_id") or incoming.infohash or incoming.id or key
                    )
                    metadata = {
                        "source_id": incoming.source_id,
                        "source_name": incoming.source_name,
                        "snapshot_date": observed_at,
                    }
                    cursor = self.connection.execute(
                        """
                        INSERT OR IGNORE INTO record_provenance
                            (dedupe_key, snapshot_id, source_record_id, observed_at, metadata_json)
                        VALUES (?, ?, ?, ?, ?)
                        """,
                        (key, snapshot_id, source_record_id, observed_at, json.dumps(metadata, separators=(",", ":"))),
                    )
                    provenance_inserted += max(0, int(cursor.rowcount))
                    processed += 1
                    if soft_limit_bytes and processed % 1000 == 0 and self.size_bytes() > soft_limit_bytes:
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
        return SnapshotMergeStats(processed, inserted, updated, provenance_inserted)

    def iter_active(self, source_ids: set[str], suppressions: Suppressions = EMPTY_SUPPRESSIONS):
        """Yield active records without loading the complete catalogue into RAM."""

        for row in self.connection.execute("SELECT payload_json, first_seen, last_seen FROM records ORDER BY dedupe_key"):
            item = normalize_item(TorrentItem.from_mapping(json.loads(row["payload_json"])))
            item.first_seen = row["first_seen"]
            item.last_seen = row["last_seen"]
            if not item_source_ids(item).intersection(source_ids):
                continue
            if suppressions.matches(item):
                continue
            yield item

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

    def count_suppressed(self, source_ids: set[str], suppressions: Suppressions = EMPTY_SUPPRESSIONS) -> int:
        if suppressions == EMPTY_SUPPRESSIONS:
            return 0
        suppressed = 0
        for row in self.connection.execute("SELECT payload_json FROM records ORDER BY dedupe_key"):
            item = normalize_item(TorrentItem.from_mapping(json.loads(row["payload_json"])))
            if item_source_ids(item).intersection(source_ids) and suppressions.matches(item):
                suppressed += 1
        return suppressed

    def begin_snapshot(self, snapshot: dict[str, Any], started_at: str) -> None:
        checksum = snapshot.get("checksum") or {}
        with self.connection:
            self.connection.execute(
                """
                INSERT INTO historical_snapshots (
                    snapshot_id, name, source_id, snapshot_date, provenance_url, archive_url,
                    checksum_algorithm, checksum_value, archive_bytes, status, started_at, error
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'running', ?, NULL)
                ON CONFLICT(snapshot_id) DO UPDATE SET
                    name = excluded.name,
                    source_id = excluded.source_id,
                    snapshot_date = excluded.snapshot_date,
                    provenance_url = excluded.provenance_url,
                    archive_url = excluded.archive_url,
                    checksum_algorithm = excluded.checksum_algorithm,
                    checksum_value = excluded.checksum_value,
                    archive_bytes = excluded.archive_bytes,
                    status = CASE WHEN historical_snapshots.status = 'complete' THEN 'complete' ELSE 'running' END,
                    started_at = COALESCE(historical_snapshots.started_at, excluded.started_at),
                    error = NULL
                """,
                (
                    snapshot["id"], snapshot["name"], snapshot["source_id"], snapshot["snapshot_date"],
                    snapshot["provenance_url"], snapshot["archive_url"], str(checksum.get("algorithm") or ""),
                    str(checksum.get("value") or ""), int(snapshot.get("expected_bytes") or 0), started_at,
                ),
            )

    def completed_snapshot_chunks(self, snapshot_id: str) -> set[str]:
        rows = self.connection.execute(
            "SELECT chunk_name FROM historical_snapshot_chunks WHERE snapshot_id = ? AND status = 'complete'",
            (snapshot_id,),
        )
        return {str(row["chunk_name"]) for row in rows}

    def record_snapshot_chunk(
        self,
        snapshot_id: str,
        chunk_name: str,
        *,
        rows_seen: int,
        inserted_count: int,
        updated_count: int,
        skipped_count: int,
        completed_at: str,
    ) -> None:
        with self.connection:
            self.connection.execute(
                """
                INSERT INTO historical_snapshot_chunks (
                    snapshot_id, chunk_name, status, rows_seen, inserted_count,
                    updated_count, skipped_count, completed_at
                ) VALUES (?, ?, 'complete', ?, ?, ?, ?, ?)
                ON CONFLICT(snapshot_id, chunk_name) DO UPDATE SET
                    status = 'complete', rows_seen = excluded.rows_seen,
                    inserted_count = excluded.inserted_count, updated_count = excluded.updated_count,
                    skipped_count = excluded.skipped_count, completed_at = excluded.completed_at
                """,
                (snapshot_id, chunk_name, rows_seen, inserted_count, updated_count, skipped_count, completed_at),
            )

    def finish_snapshot(self, snapshot_id: str, completed_at: str) -> None:
        totals = self.connection.execute(
            """
            SELECT COALESCE(SUM(rows_seen), 0), COALESCE(SUM(inserted_count), 0),
                   COALESCE(SUM(updated_count), 0), COALESCE(SUM(skipped_count), 0)
            FROM historical_snapshot_chunks WHERE snapshot_id = ? AND status = 'complete'
            """,
            (snapshot_id,),
        ).fetchone()
        with self.connection:
            self.connection.execute(
                """
                UPDATE historical_snapshots
                SET status = 'complete', rows_seen = ?, inserted_count = ?, updated_count = ?,
                    skipped_count = ?, completed_at = ?, error = NULL
                WHERE snapshot_id = ?
                """,
                (*totals, completed_at, snapshot_id),
            )

    def fail_snapshot(self, snapshot_id: str, error: str) -> None:
        with self.connection:
            self.connection.execute(
                "UPDATE historical_snapshots SET status = 'failed', error = ? WHERE snapshot_id = ?",
                (error[:2000], snapshot_id),
            )

    def historical_coverage(self) -> dict[str, Any]:
        snapshots = [
            dict(row)
            for row in self.connection.execute(
                """
                SELECT snapshot_id AS id, name, source_id, snapshot_date, provenance_url,
                       archive_url, checksum_algorithm, checksum_value, archive_bytes,
                       status, rows_seen, inserted_count, updated_count, skipped_count,
                       started_at, completed_at, error
                FROM historical_snapshots ORDER BY snapshot_date, snapshot_id
                """
            )
        ]
        historical_records = int(
            self.connection.execute("SELECT COUNT(DISTINCT dedupe_key) FROM record_provenance").fetchone()[0]
        )
        historical_only = int(
            self.connection.execute(
                """
                WITH observed AS (
                    SELECT dedupe_key, MAX(observed_at) AS latest_snapshot_at
                    FROM record_provenance GROUP BY dedupe_key
                )
                SELECT COUNT(*) FROM observed
                JOIN records r ON r.dedupe_key = observed.dedupe_key
                WHERE r.last_seen <= observed.latest_snapshot_at
                """
            ).fetchone()[0]
        )
        return {
            "completeness": "snapshot-backed+incremental" if snapshots else "incremental-only",
            "historical_record_count": historical_records,
            "historical_only_count": historical_only,
            "historical_refreshed_count": max(0, historical_records - historical_only),
            "snapshots": snapshots,
        }

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

    def list_checkpoints(self, source_id: str, key_prefix: str | None = None) -> dict[str, Any]:
        if key_prefix is None:
            rows = self.connection.execute(
                "SELECT checkpoint_key, value_json FROM checkpoints WHERE source_id = ? ORDER BY checkpoint_key",
                (source_id,),
            )
        else:
            rows = self.connection.execute(
                "SELECT checkpoint_key, value_json FROM checkpoints "
                "WHERE source_id = ? AND checkpoint_key LIKE ? ORDER BY checkpoint_key",
                (source_id, f"{key_prefix}%"),
            )
        return {str(row["checkpoint_key"]): json.loads(row["value_json"]) for row in rows}

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
