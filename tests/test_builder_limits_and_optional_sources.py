from __future__ import annotations

import json
from pathlib import Path

import yaml

from jtorrent_backend import builder as builder_module
from jtorrent_backend.builder import build_index
from jtorrent_backend.models import TorrentItem


def test_source_max_items_overrides_global_limit(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    config = {
        "settings": {
            "output_dir": "public",
            "limits": {"max_items_per_source": 1, "max_items_total": 100, "max_json_file_bytes": 100000},
        },
        "sources": [
            {
                "id": "manual_list",
                "name": "Manual List",
                "type": "direct_list",
                "enabled": True,
                "required": True,
                "category": "test",
                "max_items": 2,
                "items": [
                    {"title": "One", "torrent_url": "https://example.com/one.torrent"},
                    {"title": "Two", "torrent_url": "https://example.com/two.torrent"},
                    {"title": "Three", "torrent_url": "https://example.com/three.torrent"},
                ],
            }
        ],
    }
    config_path = tmp_path / "sources.yml"
    config_path.write_text(yaml.safe_dump(config), encoding="utf-8")

    manifest = build_index(config_path)

    assert manifest["item_count"] == 2
    sources = json.loads((tmp_path / "public" / "data" / "sources.json").read_text(encoding="utf-8"))
    assert sources[0]["item_count"] == 2
    assert sources[0]["max_items"] == 2


def test_optional_source_errors_are_warnings_not_manifest_errors(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    config = {
        "settings": {"output_dir": "public", "limits": {"max_json_file_bytes": 100000}},
        "sources": [
            {
                "id": "bad_optional",
                "name": "Bad Optional",
                "type": "does_not_exist",
                "enabled": True,
                "required": False,
                "category": "test",
            }
        ],
        "manual_items": [
            {"title": "Manual", "category": "test", "source_id": "manual", "torrent_url": "https://example.com/manual.torrent"}
        ],
    }
    config_path = tmp_path / "sources.yml"
    config_path.write_text(yaml.safe_dump(config), encoding="utf-8")

    manifest = build_index(config_path)

    assert manifest["errors"] == []
    assert any("bad_optional" in warning for warning in manifest["warnings"])
    sources = json.loads((tmp_path / "public" / "data" / "sources.json").read_text(encoding="utf-8"))
    assert sources[0]["required"] is False
    assert sources[0]["error"] == "Unknown source type: does_not_exist"


def test_builder_adds_indexed_item_count_to_sources(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    config = {
        "settings": {"output_dir": "public", "limits": {"max_json_file_bytes": 100000}},
        "sources": [
            {
                "id": "manual_list",
                "name": "Manual List",
                "type": "direct_list",
                "enabled": True,
                "required": True,
                "category": "test",
                "items": [
                    {"title": "One", "torrent_url": "https://example.com/one.torrent"},
                    {"title": "One Duplicate", "torrent_url": "https://example.com/one.torrent"},
                ],
            }
        ],
    }
    config_path = tmp_path / "sources.yml"
    config_path.write_text(yaml.safe_dump(config), encoding="utf-8")

    build_index(config_path)

    sources = json.loads((tmp_path / "public" / "data" / "sources.json").read_text(encoding="utf-8"))
    assert sources[0]["item_count"] == 2
    assert sources[0]["indexed_item_count"] == 1


def test_builder_publishes_adapter_warnings_without_failing_source(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)

    class WarningAdapter:
        def __init__(self, source, _http):
            self.source = source
            self.warnings = []

        def fetch(self):
            self.warnings.append("one uploader was deferred")
            yield TorrentItem.from_mapping(
                {
                    "title": "Retained result",
                    "category": "test",
                    "source_id": self.source["id"],
                    "source_name": self.source["name"],
                    "torrent_url": "https://example.com/retained.torrent",
                }
            )

    monkeypatch.setitem(builder_module.SOURCE_TYPES, "warning_adapter", WarningAdapter)
    config = {
        "settings": {"output_dir": "public", "limits": {"max_json_file_bytes": 100000}},
        "sources": [
            {
                "id": "partial_optional",
                "name": "Partial Optional",
                "type": "warning_adapter",
                "enabled": True,
                "required": False,
                "category": "test",
            }
        ],
    }
    config_path = tmp_path / "sources.yml"
    config_path.write_text(yaml.safe_dump(config), encoding="utf-8")

    manifest = build_index(config_path)

    assert manifest["errors"] == []
    assert "partial_optional: one uploader was deferred" in manifest["warnings"]
    sources = json.loads((tmp_path / "public" / "data" / "sources.json").read_text(encoding="utf-8"))
    assert sources[0]["warnings"] == ["one uploader was deferred"]
    assert sources[0]["error"] is None
