from __future__ import annotations

import csv
import fnmatch
import hashlib
import io
import json
import re
import shutil
import sqlite3
import subprocess
import sys
import zipfile
from pathlib import Path
from typing import Any, Iterable, Iterator, TextIO
from urllib.parse import quote

import requests
import yaml

from .catalog import CatalogStore, SnapshotMergeStats
from .models import TorrentItem
from .normalize import clean_tags
from .timeutil import utc_now_iso


CATEGORY_NAMES = {
    "100": "audio",
    "200": "movies",
    "300": "software",
    "400": "games",
    "500": "general",
    "600": "general",
}
DETAIL_HEADER = "Title"
INFOHASH_RE = re.compile(r"^[0-9a-fA-F]{40}$")
EXACT_BYTES_RE = re.compile(r"\((\d+)\s+Bytes\)", re.IGNORECASE)


class HistoricalImportError(RuntimeError):
    pass


def load_snapshot_registry(path: Path) -> dict[str, dict[str, Any]]:
    payload = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    if not isinstance(payload, dict) or payload.get("version") != 1:
        raise HistoricalImportError(f"Historical snapshot registry has an unsupported schema: {path}")
    snapshots: dict[str, dict[str, Any]] = {}
    for raw in payload.get("snapshots") or []:
        if not isinstance(raw, dict) or not str(raw.get("id") or "").strip():
            raise HistoricalImportError("Every historical snapshot needs a non-empty id.")
        snapshot = dict(raw)
        snapshot_id = str(snapshot["id"])
        if snapshot_id in snapshots:
            raise HistoricalImportError(f"Duplicate historical snapshot id: {snapshot_id}")
        required = [
            "name", "source_id", "source_name", "provenance_url", "archive_url", "archive_filename",
            "archive_format", "archive_member_glob", "snapshot_date", "expected_bytes", "checksum",
        ]
        missing = [key for key in required if snapshot.get(key) in [None, "", {}]]
        if missing:
            raise HistoricalImportError(f"Snapshot {snapshot_id} is missing: {', '.join(missing)}")
        checksum = snapshot.get("checksum") or {}
        if str(checksum.get("algorithm") or "").lower() not in hashlib.algorithms_available:
            raise HistoricalImportError(f"Snapshot {snapshot_id} uses an unsupported checksum algorithm.")
        snapshots[snapshot_id] = snapshot
    return snapshots


def _hash_file(path: Path, algorithm: str) -> str:
    digest = hashlib.new(algorithm)
    with path.open("rb") as handle:
        while block := handle.read(8 * 1024 * 1024):
            digest.update(block)
    return digest.hexdigest().lower()


def verify_archive(path: Path, snapshot: dict[str, Any]) -> None:
    expected_bytes = int(snapshot.get("expected_bytes") or 0)
    if not path.is_file():
        raise HistoricalImportError(f"Historical archive was not found: {path}")
    if expected_bytes and path.stat().st_size != expected_bytes:
        raise HistoricalImportError(
            f"Historical archive has {path.stat().st_size} bytes; expected {expected_bytes}: {path}"
        )
    checksum = snapshot.get("checksum") or {}
    algorithm = str(checksum.get("algorithm") or "").lower()
    expected = str(checksum.get("value") or "").lower()
    actual = _hash_file(path, algorithm)
    if actual != expected:
        raise HistoricalImportError(
            f"Historical archive {algorithm} mismatch; expected {expected}, got {actual}: {path}"
        )


def download_archive(snapshot: dict[str, Any], cache_dir: Path) -> Path:
    cache_dir.mkdir(parents=True, exist_ok=True)
    target = cache_dir / str(snapshot["archive_filename"])
    if target.is_file():
        verify_archive(target, snapshot)
        return target

    expected_bytes = int(snapshot.get("expected_bytes") or 0)
    free_bytes = shutil.disk_usage(cache_dir).free
    if expected_bytes and free_bytes < expected_bytes * 2:
        raise HistoricalImportError(
            f"At least {expected_bytes * 2:,} free bytes are required to download and process the archive; "
            f"only {free_bytes:,} are available."
        )

    partial = target.with_suffix(target.suffix + ".part")
    start = partial.stat().st_size if partial.is_file() else 0
    headers = {"Range": f"bytes={start}-"} if start else {}
    print(f"Downloading {snapshot['name']} to {target} (resume offset {start:,})")
    with requests.get(str(snapshot["archive_url"]), headers=headers, stream=True, timeout=(30, 180)) as response:
        response.raise_for_status()
        append = start > 0 and response.status_code == 206
        if start and not append:
            start = 0
        mode = "ab" if append else "wb"
        written = start
        next_report = written + 128 * 1024 * 1024
        with partial.open(mode) as handle:
            for block in response.iter_content(4 * 1024 * 1024):
                if not block:
                    continue
                handle.write(block)
                written += len(block)
                if written >= next_report:
                    print(f"Downloaded {written:,} bytes", flush=True)
                    next_report = written + 128 * 1024 * 1024
    partial.replace(target)
    verify_archive(target, snapshot)
    return target


def backup_catalog_once(catalog_path: Path, backup_dir: Path, snapshot_id: str) -> Path | None:
    """Create one consistent pre-import SQLite backup for a new snapshot."""

    catalog_path = Path(catalog_path)
    if not catalog_path.is_file():
        return None
    source = sqlite3.connect(catalog_path)
    try:
        table = source.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='historical_snapshots'"
        ).fetchone()
        if table:
            existing = source.execute(
                "SELECT 1 FROM historical_snapshots WHERE snapshot_id = ?", (snapshot_id,)
            ).fetchone()
            if existing:
                return None
        backup_dir.mkdir(parents=True, exist_ok=True)
        target = backup_dir / f"catalog-pre-{snapshot_id}.sqlite"
        if target.exists():
            return target
        destination = sqlite3.connect(target)
        try:
            source.backup(destination)
        finally:
            destination.close()
        return target
    finally:
        source.close()


def find_bsdtar() -> str:
    candidate = shutil.which("bsdtar") or shutil.which("tar")
    if not candidate:
        raise HistoricalImportError(
            "A libarchive-compatible bsdtar is required. Windows includes tar.exe; on Raspberry Pi install libarchive-tools."
        )
    result = subprocess.run([candidate, "--version"], capture_output=True, text=True, check=False)
    version = f"{result.stdout}\n{result.stderr}".lower()
    if result.returncode or ("bsdtar" not in version and "libarchive" not in version):
        raise HistoricalImportError(
            f"{candidate} is not libarchive-compatible. Install bsdtar/libarchive-tools to read the nested 7z files."
        )
    return candidate


def parse_detail_stream(stream: TextIO) -> Iterator[dict[str, str]]:
    """Parse concatenated one-record TPB details.csv files with repeated headers."""

    reader = csv.reader(stream)
    header: list[str] | None = None
    for raw_row in reader:
        row = [str(value).lstrip("\ufeff") if index == 0 else str(value) for index, value in enumerate(raw_row)]
        if not row or not any(value.strip() for value in row):
            continue
        if row[0].strip() == DETAIL_HEADER:
            header = [value.strip() for value in row]
            continue
        if header is None:
            continue
        if len(row) < len(header):
            row.extend([""] * (len(header) - len(row)))
        yield {header[index]: row[index].strip() for index in range(len(header))}


def iter_chunk_details(chunk: Path, bsdtar: str) -> Iterator[dict[str, str]]:
    process = subprocess.Popen(
        [bsdtar, "-xOf", str(chunk), "*/details.csv"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if process.stdout is None or process.stderr is None:
        process.kill()
        raise HistoricalImportError(f"Unable to open archive reader for {chunk}")
    stream = io.TextIOWrapper(process.stdout, encoding="utf-8-sig", errors="replace", newline="")
    try:
        yield from parse_detail_stream(stream)
    finally:
        stream.close()
    stderr = process.stderr.read().decode("utf-8", errors="replace").strip()
    return_code = process.wait()
    if return_code:
        raise HistoricalImportError(f"bsdtar failed for {chunk}: {stderr or f'exit {return_code}'}")


def _number(value: Any) -> int | None:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


def _size_bytes(value: Any) -> int | None:
    match = EXACT_BYTES_RE.search(str(value or ""))
    return int(match.group(1)) if match else None


def detail_to_item(row: dict[str, str], snapshot: dict[str, Any]) -> TorrentItem | None:
    title = str(row.get("Title") or "").strip()
    infohash = str(row.get("Info Hash") or "").strip().lower()
    if not title or not INFOHASH_RE.fullmatch(infohash):
        return None
    raw_category = str(row.get("Type") or "").strip()
    broad_category = CATEGORY_NAMES.get(raw_category[:1] + "00", "general")
    uploader = str(row.get("By") or "").strip()
    uploaded = str(row.get("Uploaded") or "").strip() or None
    capture_date = str(row.get("Capture Date") or snapshot["snapshot_date"]).strip()
    tags = ["historical", "the pirate bay", broad_category]
    if uploader:
        tags.append(uploader)
    return TorrentItem.from_mapping({
        "title": title,
        "category": broad_category,
        "source_id": snapshot["source_id"],
        "source_name": snapshot["source_name"],
        "source_homepage": snapshot.get("source_homepage"),
        "source_url": snapshot.get("source_homepage"),
        "details_url": snapshot.get("source_homepage"),
        "magnet": f"magnet:?xt=urn:btih:{infohash}&dn={quote(title)}",
        "infohash": infohash,
        "hash_source": snapshot["id"],
        "size_bytes": _size_bytes(row.get("Size")),
        "seeders": _number(row.get("Seeders")),
        "leechers": _number(row.get("Leechers")),
        "date_added": uploaded,
        "copyright_status": snapshot.get("copyright_status", "unknown"),
        "policy_scope": snapshot.get("policy_scope", "third-party-unverified"),
        "tags": clean_tags(tags),
        "first_seen": capture_date,
        "last_seen": capture_date,
        "fetched_at": capture_date,
        "raw": {
            "historical_snapshot_id": snapshot["id"],
            "archive_record_id": infohash,
            "category_id": raw_category,
            "file_count": _number(row.get("Files")),
            "uploader": uploader or None,
            "capture_date": capture_date,
        },
    })


def _merge_rows(
    store: CatalogStore,
    rows: Iterable[dict[str, str]],
    snapshot: dict[str, Any],
    *,
    soft_limit_bytes: int,
    batch_size: int,
) -> tuple[int, int, int, int]:
    rows_seen = inserted = updated = skipped = 0
    batch: list[TorrentItem] = []

    def flush() -> SnapshotMergeStats | None:
        nonlocal batch, inserted, updated
        if not batch:
            return None
        stats = store.merge_snapshot(
            batch,
            snapshot_id=str(snapshot["id"]),
            observed_at=str(snapshot["snapshot_date"]),
            soft_limit_bytes=soft_limit_bytes or None,
        )
        inserted += stats.inserted
        updated += stats.updated
        batch = []
        return stats

    for row in rows:
        rows_seen += 1
        item = detail_to_item(row, snapshot)
        if item is None:
            skipped += 1
            continue
        batch.append(item)
        if len(batch) >= batch_size:
            flush()
    flush()
    return rows_seen, inserted, updated, skipped


def import_snapshot(
    snapshot: dict[str, Any],
    *,
    archive: Path,
    catalog_path: Path,
    cache_dir: Path,
    soft_limit_bytes: int,
    batch_size: int = 1000,
    max_chunks: int | None = None,
    archive_verified: bool = False,
) -> dict[str, Any]:
    if snapshot.get("archive_format") != "tpb-details-7z-in-zip":
        raise HistoricalImportError(f"Unsupported archive format: {snapshot.get('archive_format')}")
    if not archive_verified:
        verify_archive(archive, snapshot)
    bsdtar = find_bsdtar()
    started_at = utc_now_iso()
    chunk_dir = cache_dir / "chunks" / str(snapshot["id"])
    chunk_dir.mkdir(parents=True, exist_ok=True)

    with CatalogStore(catalog_path) as store:
        store.begin_snapshot(snapshot, started_at)
        completed = store.completed_snapshot_chunks(str(snapshot["id"]))
        processed_now = 0
        try:
            with zipfile.ZipFile(archive) as outer:
                member_glob = str(snapshot["archive_member_glob"])
                members = sorted(
                    [
                        info for info in outer.infolist()
                        if not info.is_dir() and fnmatch.fnmatch(info.filename, member_glob)
                    ],
                    key=lambda info: info.filename,
                )
                if not members:
                    raise HistoricalImportError(
                        f"No nested data chunks matched {member_glob!r} in {archive}"
                    )
                for index, info in enumerate(members, 1):
                    if info.filename in completed:
                        continue
                    if max_chunks is not None and processed_now >= max_chunks:
                        break
                    chunk = chunk_dir / Path(info.filename).name
                    print(f"Importing chunk {index}/{len(members)}: {info.filename}", flush=True)
                    with outer.open(info) as source, chunk.open("wb") as destination:
                        shutil.copyfileobj(source, destination, length=4 * 1024 * 1024)
                    try:
                        stats = _merge_rows(
                            store,
                            iter_chunk_details(chunk, bsdtar),
                            snapshot,
                            soft_limit_bytes=soft_limit_bytes,
                            batch_size=batch_size,
                        )
                    finally:
                        chunk.unlink(missing_ok=True)
                    store.record_snapshot_chunk(
                        str(snapshot["id"]), info.filename,
                        rows_seen=stats[0], inserted_count=stats[1], updated_count=stats[2],
                        skipped_count=stats[3], completed_at=utc_now_iso(),
                    )
                    processed_now += 1
                    print(
                        f"Chunk complete: {stats[0]:,} rows, {stats[1]:,} inserted, "
                        f"{stats[2]:,} merged, {stats[3]:,} skipped",
                        flush=True,
                    )
                all_complete = len(store.completed_snapshot_chunks(str(snapshot["id"]))) == len(members)
                if all_complete:
                    store.finish_snapshot(str(snapshot["id"]), utc_now_iso())
                coverage = store.historical_coverage()
        except Exception as exc:
            store.fail_snapshot(str(snapshot["id"]), str(exc))
            raise

    result = {
        "snapshot_id": snapshot["id"],
        "archive": str(archive.resolve()),
        "processed_chunks_this_run": processed_now,
        "complete": all_complete,
        "coverage": coverage,
    }
    print(json.dumps(result, ensure_ascii=False, separators=(",", ":")))
    return result
