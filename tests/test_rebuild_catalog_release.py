import json

import pytest

from jtorrent_backend.catalog import CatalogStore
from jtorrent_backend.models import TorrentItem
from rebuild_catalog_release import restore_manual_scopes, verify_baseline


def test_manual_scope_repair_preserves_observation_dates(tmp_path):
    raw = {"title": "Official ISO", "source_id": "manual", "torrent_url": "https://example.org/linux.torrent", "category": "linux"}
    with CatalogStore(tmp_path / "clone.sqlite") as store:
        store.merge([TorrentItem.from_mapping(raw)], "2026-08-29T00:00:00Z")
        before = dict(store.connection.execute("SELECT * FROM records").fetchone())
        assert restore_manual_scopes(store, [{**raw, "policy_scope": "open-authorized"}]) == 1
        after = dict(store.connection.execute("SELECT * FROM records").fetchone())
        expected = json.loads(before.pop("payload_json"))
        actual = json.loads(after.pop("payload_json"))
        assert before == after
        expected["policy_scope"] = "open-authorized"
        assert actual == expected
        assert restore_manual_scopes(store, [{**raw, "policy_scope": "open-authorized"}]) == 0


def baseline():
    return {"manifest": {"generated_at": "old", "search_contract": "v4", "item_count": 2, "counts_by_scope": {"open-authorized": 2}},
            "status": {"generated_at": "old", "release_id": "v4-old", "counts_by_scope": {"open-authorized": 2}},
            "current": {"release_id": "v4-old", "item_count": 2},
            "sources": [{"id": "official"}], "source-health": {"generated_at": "old", "sources": [{"id": "official"}]}}


def test_baseline_accepts_consistent_snapshot():
    verify_baseline(baseline(), {"official"})


@pytest.mark.parametrize("part,key,value", [("status", "generated_at", "new"), ("current", "release_id", "v4-other"),
                                          ("source-health", "generated_at", None), ("manifest", "errors", ["failure"])])
def test_rebuild_rejects_inconsistent_baseline(part, key, value):
    sample = baseline()
    sample[part][key] = value
    with pytest.raises(ValueError):
        verify_baseline(sample, {"official"})
