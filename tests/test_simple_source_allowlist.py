from __future__ import annotations

import json
from pathlib import Path

import yaml

from jtorrent_backend.builder import build_index


def test_build_index_does_not_require_policy_status(tmp_path: Path) -> None:
    output_dir = tmp_path / "public"
    config = {
        "settings": {
            "name": "Test Backend",
            "output_dir": str(output_dir),
            "limits": {
                "max_items_total": 10,
                "max_items_per_source": 10,
                "max_json_file_bytes": 5 * 1024 * 1024,
            },
        },
        "sources": [
            {
                "id": "disabled_source_without_status",
                "name": "Disabled Source Without Status",
                "type": "direct_list",
                "enabled": False,
                "category": "test",
                "items": [
                    {
                        "title": "Should Not Appear",
                        "source_url": "https://disabled.example/item",
                    }
                ],
            },
            {
                "id": "enabled_source_without_status",
                "name": "Enabled Source Without Status",
                "type": "direct_list",
                "enabled": True,
                "category": "test",
                "items": [
                    {
                        "title": "Should Appear",
                        "source_url": "https://enabled.example/item",
                        "torrent_url": "https://enabled.example/item.torrent",
                    }
                ],
            },
        ],
        "manual_items": [
            {
                "title": "Manual Item Without Status",
                "category": "manual",
                "source_id": "manual",
                "source_name": "Manual",
                "source_url": "https://manual.example/item",
            }
        ],
    }
    config_path = tmp_path / "sources.yml"
    config_path.write_text(yaml.safe_dump(config), encoding="utf-8")

    manifest = build_index(config_path)

    assert manifest["source_allowlist_mode"] == "enabled-sources-only"
    assert "policy" not in manifest
    assert manifest["enabled_source_count"] == 1
    assert manifest["item_count"] == 2

    items = json.loads((output_dir / "data" / "search-index.json").read_text(encoding="utf-8"))
    titles = {item["title"] for item in items}
    assert "Should Appear" in titles
    assert "Manual Item Without Status" in titles
    assert "Should Not Appear" not in titles
