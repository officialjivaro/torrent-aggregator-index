from __future__ import annotations

import json
from pathlib import Path

from scripts.jtorrent_backend.search_policy import apply_policy
from scripts.jtorrent_backend.search_v3 import build_search_v3, tokenize


def write_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False) + "\n", encoding="utf-8")


def make_public(root: Path) -> Path:
    public = root / "public"
    docs = [
        {
            "doc_id": 0,
            "title": "Public Domain Film Archive",
            "category": "public-domain",
            "source_id": "archive_public_domain",
            "source_name": "Internet Archive",
            "details_url": "https://archive.org/details/example-film",
            "torrent_url": "https://archive.org/download/example-film/example-film_archive.torrent",
            "copyright_status": "public-domain",
            "tags": ["public", "domain", "archive", "film"],
            "description": "A public domain film distributed by the Internet Archive.",
        },
        {
            "doc_id": 1,
            "title": "Creative Commons Audio Collection",
            "category": "creative-commons",
            "source_id": "archive_cc_audio",
            "source_name": "Internet Archive",
            "details_url": "https://archive.org/details/example-audio",
            "copyright_status": "creative-commons",
            "tags": ["creative", "commons", "audio"],
        },
        {
            "doc_id": 2,
            "title": "Ubuntu Official ISO Torrent",
            "category": "linux",
            "source_id": "ubuntu_official",
            "source_name": "Official Ubuntu Releases",
            "details_url": "https://releases.ubuntu.com/",
            "copyright_status": "open-source",
            "tags": ["ubuntu", "official", "linux", "iso", "torrent"],
        },
        {
            "doc_id": 3,
            "title": "Academic Torrents Research Dataset",
            "category": "datasets",
            "source_id": "academic_torrents",
            "source_name": "Academic Torrents",
            "details_url": "https://academictorrents.com/details/example",
            "copyright_status": "open-data",
            "tags": ["academic", "research", "dataset", "torrent"],
        },
    ]
    write_json(public / "data/v2/docs/docs-0001.json", docs)
    write_json(public / "data/v2/manifest.json", {
        "version": 2,
        "doc_shards": [{"path": "data/v2/docs/docs-0001.json", "count": len(docs), "start_doc_id": 0, "end_doc_id": len(docs) - 1}],
    })
    write_json(public / "data/manifest.json", {
        "generated_at": "2026-08-05T00:00:00Z",
        "item_count": len(docs),
        "enabled_source_count": 4,
        "search_v2": {"version": 2},
        "sharding": {
            "legacy_active": True,
            "full_shards": [{"path": "data/shards/full/full-0001.json"}],
            "compact_shards": [{"path": "data/shards/compact/compact-0001.json"}],
            "search_shards": [{"path": "data/search/search-0001.json"}],
            "source_shards": [],
        },
    })
    write_json(public / "data/sources.json", [
        {"id": "archive_public_domain", "name": "Internet Archive public domain", "required": True, "category": "public-domain", "item_count": 1, "indexed_item_count": 1, "error": None},
        {"id": "archive_cc_audio", "name": "Internet Archive CC audio", "required": True, "category": "creative-commons", "item_count": 1, "indexed_item_count": 1, "error": None},
        {"id": "ubuntu_official", "name": "Ubuntu", "required": True, "category": "linux", "item_count": 1, "indexed_item_count": 1, "error": None},
        {"id": "academic_torrents", "name": "Academic Torrents", "required": False, "category": "datasets", "item_count": 1, "indexed_item_count": 1, "error": None},
    ])
    for relative in ["data/shards/full/full-0001.json", "data/shards/compact/compact-0001.json", "data/search/search-0001.json"]:
        write_json(public / relative, docs)
    return public


def postings_for(public: Path, token: str, version: int = 3):
    bucket = "".join(ch for ch in token if ch.isalnum())[:2].ljust(2, "_") or "__"
    return json.loads((public / f"data/v{version}/tokens/{bucket}.json").read_text(encoding="utf-8"))["tokens"][token]


def test_meaningful_domain_terms_remain_searchable():
    assert tokenize("public domain") == ["public", "domain"]
    assert tokenize("creative commons movie") == ["creative", "commons", "movie"]
    assert tokenize("Internet Archive") == ["internet", "archive"]
    assert tokenize("Ubuntu official ISO torrent") == ["ubuntu", "official", "iso", "torrent"]
    assert tokenize("Academic Torrents research") == ["academic", "torrents", "research"]


def test_v3_build_is_compact_filterable_and_v2_compatible(tmp_path: Path):
    public = make_public(tmp_path)
    result = build_search_v3(public, {
        "search_v3": {
            "enabled": True,
            "doc_shard_bytes": 65536,
            "token_bucket_prefix_chars": 2,
            "max_query_tokens": 8,
            "max_tokens_per_record": 32,
            "max_postings_per_token": 50000,
        }
    })
    assert result["status"]["search_contract"] == "v3"
    assert result["manifest"]["version"] == 3
    assert result["v2_compat"]["type"] == "v3-compatibility-token-index"

    root_manifest = json.loads((public / "data/manifest.json").read_text(encoding="utf-8"))
    assert root_manifest["search_contract"] == "v3"
    assert root_manifest["search_v3"]["manifest_path"] == "data/v3/manifest.json"

    v3 = json.loads((public / "data/v3/manifest.json").read_text(encoding="utf-8"))
    v2 = json.loads((public / "data/v2/manifest.json").read_text(encoding="utf-8"))
    assert v3["posting_format"] == ["doc_id", "category_index", "source_index", "static_score"]
    assert v2["doc_shards"] == v3["doc_shards"]
    assert all(item["path"].startswith("data/v3/docs/") for item in v2["doc_shards"])

    public_index = v3["categories"].index("public-domain")
    creative_index = v3["categories"].index("creative-commons")
    public_posting = postings_for(public, "public", 3)
    assert public_posting[0][:3] == [0, public_index, v3["sources"].index("archive_public_domain")]
    assert public_posting[0][3] > 0
    creative_posting = postings_for(public, "creative", 3)
    assert creative_posting[0][:3] == [1, creative_index, v3["sources"].index("archive_cc_audio")]
    assert creative_posting[0][3] > 0
    assert postings_for(public, "public", 2) == [0]

    status = json.loads((public / "data/status.json").read_text(encoding="utf-8"))
    assert status["item_count"] == 4
    assert status["healthy_source_count"] == 4
    assert status["failed_source_count"] == 0

    assert not (public / "data/v2/docs").exists()
    for relative in ["data/shards/full", "data/shards/compact", "data/search"]:
        assert not (public / relative).exists()
    assert max(item["bytes"] for item in v3["doc_shards"]) <= 65536


def test_public_search_policy_disables_only_named_sources():
    config = {
        "settings": {"output": {"legacy_shards": True}},
        "sources": [
            {"id": "ubuntu_official", "name": "Ubuntu", "enabled": True, "required": True},
            {"id": "uncertain_index", "name": "Index", "enabled": True, "required": False},
        ],
    }
    policy = {
        "disabledSources": {"uncertain_index": "Collection-level authorization is not established."},
        "output": {"legacy_shards": False, "search_v3": {"enabled": True}},
    }
    effective, disabled = apply_policy(config, policy)
    by_id = {source["id"]: source for source in effective["sources"]}
    assert by_id["ubuntu_official"]["enabled"] is True
    assert by_id["uncertain_index"]["enabled"] is False
    assert by_id["uncertain_index"]["required"] is False
    assert effective["settings"]["output"]["legacy_shards"] is False
    assert disabled == [{
        "id": "uncertain_index",
        "name": "Index",
        "state": "disabled-by-policy",
        "reason": "Collection-level authorization is not established.",
        "category": None,
    }]
