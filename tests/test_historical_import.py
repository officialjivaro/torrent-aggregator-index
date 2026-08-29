from __future__ import annotations

import hashlib
import io
import zipfile
from pathlib import Path

from jtorrent_backend.catalog import CatalogStore
from jtorrent_backend.historical import (
    detail_to_item,
    import_snapshot,
    parse_detail_stream,
)
from jtorrent_backend.models import TorrentItem


def snapshot_for(archive: Path) -> dict:
    return {
        "id": "tpb-test-snapshot",
        "name": "TPB test snapshot",
        "source_id": "the_pirate_bay",
        "source_name": "The Pirate Bay",
        "source_homepage": "https://thepiratebay.org/",
        "provenance_url": "https://archive.org/details/example",
        "archive_url": "https://archive.org/download/example/archive.zip",
        "archive_filename": archive.name,
        "archive_format": "tpb-details-7z-in-zip",
        "archive_member_glob": "*/data/*/*.7z",
        "snapshot_date": "2012-10-06T00:00:00Z",
        "expected_bytes": archive.stat().st_size,
        "checksum": {"algorithm": "sha1", "value": hashlib.sha1(archive.read_bytes()).hexdigest()},
        "copyright_status": "unknown",
        "policy_scope": "third-party-unverified",
    }


def detail_row(identifier: int, *, seeders: str = "3") -> dict[str, str]:
    return {
        "Title": f"Archive torrent {identifier}",
        "Type": "601",
        "Files": "2",
        "Size": "1.00 KiB (1024 Bytes)",
        "Uploaded": "2011-01-02T03:04:05Z",
        "By": "Uploader",
        "Seeders": seeders,
        "Leechers": "1",
        "Info Hash": f"{identifier:040x}",
        "Capture Date": "2012-09-15T14:54:50Z",
    }


def test_parser_handles_repeated_bom_headers_and_exact_size() -> None:
    csv_text = (
        "\ufeffTitle,Type,Files,Size,Uploaded,By,Seeders,Leechers,Info Hash,Capture Date\n"
        "First,601,1,1.00 KiB (1024 Bytes),2011-01-01T00:00:00Z,A,2,1,"
        "0000000000000000000000000000000000000001,2012-01-01T00:00:00Z\n"
        "Title,Type,Files,Size,Uploaded,By,Seeders,Leechers,Info Hash,Capture Date\n"
        "Second,301,2,2.00 KiB (2048 Bytes),2011-01-02T00:00:00Z,B,3,0,"
        "0000000000000000000000000000000000000002,2012-01-02T00:00:00Z\n"
    )
    rows = list(parse_detail_stream(io.StringIO(csv_text)))
    fake_archive = Path("archive.zip")
    snapshot = {
        "id": "snapshot", "source_id": "the_pirate_bay", "source_name": "The Pirate Bay",
        "source_homepage": "https://thepiratebay.org/", "snapshot_date": "2012-10-06T00:00:00Z",
        "policy_scope": "third-party-unverified", "copyright_status": "unknown",
    }
    items = [detail_to_item(row, snapshot) for row in rows]
    assert [item.title for item in items if item] == ["First", "Second"]
    assert [item.size_bytes for item in items if item] == [1024, 2048]
    assert items[0].raw == {
        "historical_snapshot_id": "snapshot",
        "archive_record_id": "0000000000000000000000000000000000000001",
        "category_id": "601",
        "file_count": 1,
        "uploader": "A",
        "capture_date": "2012-01-01T00:00:00Z",
    }


def test_snapshot_import_is_chunk_resumable_and_preserves_current_stats(tmp_path: Path, monkeypatch) -> None:
    archive = tmp_path / "archive.zip"
    with zipfile.ZipFile(archive, "w") as outer:
        outer.writestr("root/data/32/32xxxxx.7z", b"first")
        outer.writestr("root/data/33/33xxxxx.7z", b"second")
    snapshot = snapshot_for(archive)
    rows_by_chunk = {
        "32xxxxx.7z": [detail_row(1, seeders="3")],
        "33xxxxx.7z": [detail_row(2, seeders="4")],
    }
    monkeypatch.setattr("jtorrent_backend.historical.find_bsdtar", lambda: "bsdtar")
    monkeypatch.setattr(
        "jtorrent_backend.historical.iter_chunk_details",
        lambda chunk, _bsdtar: iter(rows_by_chunk[chunk.name]),
    )
    catalog = tmp_path / "catalog.sqlite"

    first = import_snapshot(
        snapshot, archive=archive, catalog_path=catalog, cache_dir=tmp_path / "cache",
        soft_limit_bytes=1024 * 1024 * 1024, max_chunks=1,
    )
    assert first["complete"] is False
    second = import_snapshot(
        snapshot, archive=archive, catalog_path=catalog, cache_dir=tmp_path / "cache",
        soft_limit_bytes=1024 * 1024 * 1024,
    )
    assert second["complete"] is True
    with CatalogStore(catalog) as store:
        assert store.count() == 2
        assert store.historical_coverage()["historical_record_count"] == 2
        repeated = store.merge_snapshot(
            [detail_to_item(detail_row(1, seeders="3"), snapshot)],
            snapshot_id=snapshot["id"],
            observed_at=snapshot["snapshot_date"],
        )
        assert repeated.provenance_inserted == 0
        assert store.historical_coverage()["historical_record_count"] == 2
        current = TorrentItem.from_mapping({
            **detail_to_item(detail_row(1, seeders="3"), snapshot).to_dict(),
            "seeders": 99,
            "last_seen": "2026-08-30T00:00:00Z",
            "fetched_at": "2026-08-30T00:00:00Z",
        })
        store.merge([current], "2026-08-30T00:00:00Z")
        active = list(store.iter_active({"the_pirate_bay"}))
        assert next(item for item in active if item.infohash == f"{1:040x}").seeders == 99
