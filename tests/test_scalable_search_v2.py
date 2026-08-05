from __future__ import annotations

import json
from pathlib import Path

from jtorrent_backend.models import TorrentItem
from jtorrent_backend.output import write_public


def test_write_public_creates_scalable_v2_token_index(tmp_path: Path) -> None:
    items = [
        TorrentItem(
            id="ubuntu",
            slug="ubuntu",
            title="Ubuntu Linux ISO",
            category="linux",
            source_id="ubuntu_official",
            source_name="Ubuntu",
            torrent_url="https://example.com/ubuntu.torrent",
            description="Ubuntu installer image",
            tags=["linux", "iso"],
        ),
        TorrentItem(
            id="debian",
            slug="debian",
            title="Debian Linux ISO",
            category="linux",
            source_id="debian_official",
            source_name="Debian",
            torrent_url="https://example.com/debian.torrent",
            description="Debian installer image",
            tags=["linux", "iso"],
        ),
    ]
    manifest = {"base_url": "https://example.com/jtorrent", "item_count": len(items), "enabled_source_count": 1}

    write_public(
        tmp_path / "public",
        items,
        [],
        manifest,
        max_file_bytes=2000,
        output_options={"scalable_search": {"enabled": True}, "legacy_shards": True, "legacy_max_items": 100},
    )

    data_dir = tmp_path / "public" / "data"
    generated_manifest = json.loads((data_dir / "manifest.json").read_text(encoding="utf-8"))
    v2 = generated_manifest["search_v2"]
    assert v2["version"] == 2
    assert v2["doc_count"] == 2
    assert v2["doc_shards"]
    assert v2["token_buckets"]

    ubuntu_bucket = json.loads((tmp_path / "public" / "data" / "v2" / "tokens" / "ub.json").read_text(encoding="utf-8"))
    assert ubuntu_bucket["tokens"]["ubuntu"] == [0]
    assert (tmp_path / "public" / "jtorrent-search-v2.js").exists()


def test_write_public_turns_legacy_indexes_into_v2_pointers_when_too_large(tmp_path: Path) -> None:
    items = [TorrentItem(id=f"i{i}", title=f"Item {i} Ubuntu", source_id="s", category="linux") for i in range(3)]
    manifest = {"base_url": "https://example.com/jtorrent", "item_count": len(items), "enabled_source_count": 1}

    write_public(
        tmp_path / "public",
        items,
        [],
        manifest,
        max_file_bytes=2000,
        output_options={"scalable_search": {"enabled": True}, "legacy_shards": True, "legacy_max_items": 2},
    )

    data_dir = tmp_path / "public" / "data"
    compact = json.loads((data_dir / "search-index.min.json").read_text(encoding="utf-8"))
    manifest_payload = json.loads((data_dir / "manifest.json").read_text(encoding="utf-8"))
    assert compact["mode"] == "v2-only"
    assert compact["search_v2_manifest_path"] == "data/v2/manifest.json"
    assert manifest_payload["sharding"]["legacy_active"] is False
