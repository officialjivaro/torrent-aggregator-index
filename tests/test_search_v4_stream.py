from __future__ import annotations

import json
from pathlib import Path

from jtorrent_backend.catalog import CatalogStore, EMPTY_SUPPRESSIONS
from jtorrent_backend.models import TorrentItem
from jtorrent_backend.search_v4_stream import build_search_v4_stream


def test_streaming_builder_writes_only_v4_contract(tmp_path: Path) -> None:
    catalog = tmp_path / "catalog.sqlite"
    with CatalogStore(catalog) as store:
        store.merge([
            TorrentItem.from_mapping({
                "title": "Ubuntu Linux ISO", "source_id": "ubuntu", "source_name": "Ubuntu",
                "category": "linux", "infohash": f"{1:040x}",
                "magnet": f"magnet:?xt=urn:btih:{1:040x}", "policy_scope": "open-authorized",
            }),
            TorrentItem.from_mapping({
                "title": "Archive movie", "source_id": "the_pirate_bay", "source_name": "The Pirate Bay",
                "category": "movies", "infohash": f"{2:040x}",
                "magnet": f"magnet:?xt=urn:btih:{2:040x}", "policy_scope": "third-party-unverified",
            }),
        ], "2026-08-30T00:00:00Z")
        public = tmp_path / "public"
        root = build_search_v4_stream(
            store,
            {"ubuntu", "the_pirate_bay"},
            EMPTY_SUPPRESSIONS,
            [
                {"id": "ubuntu", "name": "Ubuntu", "required": True, "item_count": 1, "warnings": [], "error": None},
                {"id": "the_pirate_bay", "name": "The Pirate Bay", "required": False, "item_count": 1, "warnings": [], "error": None},
            ],
            {
                "_output_dir": str(public), "generated_at": "2026-08-30T00:00:00Z",
                "base_url": "https://index.jtorrent.net", "custom_domain": "index.jtorrent.net",
                "enabled_source_count": 2, "warnings": [], "errors": [],
                "policy_scopes": {"open-authorized": {}, "third-party-unverified": {}},
                "default_index_scope": "open-authorized",
            },
            {"v4_only": True, "search_v4": {"enabled": True, "posting_chunk_entries": 100}},
        )
    assert root["item_count"] == 2
    assert (public / "data/v4/current.json").is_file()
    assert not (public / "data/v3").exists()
    assert not (public / "data/v2").exists()
    current = json.loads((public / "data/v4/current.json").read_text(encoding="utf-8"))
    manifest = json.loads((public / current["manifest_path"]).read_text(encoding="utf-8"))
    assert manifest["doc_count"] == 2
    assert manifest["posting_format"] == ["doc_id", "category_index", "source_index", "scope_index", "static_score"]
    assert set(manifest["scopes"]) == {"open-authorized", "third-party-unverified"}
