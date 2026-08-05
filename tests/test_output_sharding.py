from __future__ import annotations

import json
from pathlib import Path

from jtorrent_backend.models import TorrentItem
from jtorrent_backend.output import write_public


def test_write_public_creates_size_limited_shards(tmp_path: Path) -> None:
    items = [
        TorrentItem(
            id=f"item-{i}",
            slug=f"item-{i}",
            title=f"Example Item {i}",
            category="datasets" if i % 2 else "linux",
            source_id="example_source",
            source_name="Example Source",
            source_url="https://example.com/source",
            torrent_url=f"https://example.com/item-{i}.torrent",
            copyright_status="authorized",
            description="x" * 500,
            tags=["example", "test"],
            fetched_at="2026-06-03T00:00:00Z",
        )
        for i in range(12)
    ]
    manifest = {
        "name": "Test Backend",
        "generated_at": "2026-06-03T00:00:00Z",
        "base_url": "https://example.com/jtorrent",
        "item_count": len(items),
        "enabled_source_count": 1,
    }

    write_public(tmp_path / "public", items, [], manifest, max_file_bytes=2000)

    data_dir = tmp_path / "public" / "data"
    written_manifest = json.loads((data_dir / "manifest.json").read_text())
    assert written_manifest["sharding"]["enabled"] is True
    assert len(written_manifest["sharding"]["compact_shards"]) > 1
    assert len(written_manifest["sharding"]["full_shards"]) > 1
    assert len(written_manifest["sharding"]["search_shards"]) > 1

    compact_index = json.loads((data_dir / "search-index.min.json").read_text())
    assert compact_index["mode"] == "sharded"

    for shard_group in ["compact_shards", "full_shards", "search_shards"]:
        for shard in written_manifest["sharding"][shard_group]:
            path = tmp_path / "public" / shard["path"]
            assert path.exists()
            assert path.stat().st_size <= 2000
