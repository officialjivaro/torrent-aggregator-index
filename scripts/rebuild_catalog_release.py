#!/usr/bin/env python3
"""Re-index a catalog snapshot without collecting sources or refreshing health dates."""
from __future__ import annotations

import argparse
import copy
import json
import sqlite3
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from urllib.request import Request, urlopen

from jtorrent_backend.catalog import CatalogStore, load_suppressions
from jtorrent_backend.config import load_config
from jtorrent_backend.dedupe import dedupe_key
from jtorrent_backend.models import TorrentItem
from jtorrent_backend.normalize import normalize_item
from jtorrent_backend.search_integrity import required_scope
from jtorrent_backend.search_policy import apply_policy, validate_policy_contract
from jtorrent_backend.search_v3 import _write_json
from jtorrent_backend.search_v4_stream import _first_pass, build_search_v4_stream
from publish_release import validate_candidate

ROOT = Path(__file__).resolve().parents[1]
BASE = "https://index.jtorrent.net/"


def fetch_json(relative: str):
    request = Request(BASE + relative, headers={"User-Agent": "JTorrentReleaseCheck/1.0 (+https://www.jtorrent.net)"})
    with urlopen(request, timeout=45) as response:
        return json.load(response)


def restore_manual_scopes(store: CatalogStore, manual_items: list[dict]) -> int:
    """Apply only explicit canonical manual-item scopes to the disposable clone."""
    changed = 0
    with store.connection:
        for raw in manual_items:
            scope = required_scope(raw.get("policy_scope"))
            key = dedupe_key(normalize_item(TorrentItem.from_mapping(raw)))
            row = store.connection.execute("SELECT payload_json FROM records WHERE dedupe_key=?", (key,)).fetchone()
            if row is None:
                raise ValueError("Canonical manual item is missing from the catalog snapshot")
            payload = json.loads(row[0])
            if payload.get("policy_scope"):
                required_scope(payload["policy_scope"])
                continue
            if payload.get("source_id") != raw.get("source_id"):
                raise ValueError("Manual-item provenance differs from canonical configuration")
            payload["policy_scope"] = scope
            store.connection.execute("UPDATE records SET payload_json=? WHERE dedupe_key=?",
                                     (json.dumps(payload, ensure_ascii=False, separators=(",", ":")), key))
            changed += 1
    return changed


def verify_baseline(baseline: dict, active_ids: set[str]) -> None:
    root, status, health = (baseline[name] for name in ("manifest", "status", "source-health"))
    current = baseline["current"]
    if root.get("errors") or root.get("search_contract") != "v4":
        raise ValueError("Live baseline is not a successful v4 collection")
    if not root.get("generated_at") or root["generated_at"] != status.get("generated_at"):
        raise ValueError("Live baseline metadata is not from one release")
    if current.get("release_id") != status.get("release_id") or current.get("item_count") != root.get("item_count"):
        raise ValueError("Live pointer and collection metadata disagree")
    if status.get("counts_by_scope") != root.get("counts_by_scope"):
        raise ValueError("Live scope totals disagree")
    if not health.get("generated_at"):
        raise ValueError("Source-health timestamp is missing")
    source_ids = {row["id"] for row in baseline["sources"]}
    if source_ids != active_ids or {row["id"] for row in health["sources"]} != active_ids:
        raise ValueError("Live source inventory differs from current approved configuration")


def main() -> None:
    config = load_config(ROOT / "config/sources.yml")
    policy = json.loads((ROOT / "config/search-policy.json").read_text(encoding="utf-8"))
    validate_policy_contract(config, policy)
    config, disabled = apply_policy(config, policy)
    settings = config["settings"]
    source_ids = {row["id"] for row in config["sources"] if row.get("enabled", True)}
    baseline = {name: fetch_json(f"data/{name}.json") for name in ("manifest", "status", "source-health", "sources")}
    baseline["current"] = fetch_json("data/v4/current.json")
    verify_baseline(baseline, source_ids)
    catalog = (ROOT / settings["state"]["path"]).resolve()
    if not catalog.is_relative_to(ROOT / "state") or not catalog.is_file():
        raise ValueError("Expected existing project catalog under state/")
    staging = ROOT / "staging"
    staging.mkdir(exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix="catalog-release-", dir=staging))
    _write_json(work / "baseline.json", baseline, pretty=True)
    # SQLite backup gives a consistent snapshot, including committed WAL data.
    original = sqlite3.connect(catalog.as_uri() + "?mode=ro", uri=True)
    clone = sqlite3.connect(work / "catalog.sqlite")
    try:
        original.backup(clone)
    finally:
        clone.close()
        original.close()
    suppressions = load_suppressions(ROOT / settings["suppression_path"])
    all_ids = source_ids | {row["source_id"] for row in config["manual_items"]}
    public = work / "public"  # Fresh path: the builder cannot remove an existing output.
    with CatalogStore(work / "catalog.sqlite") as store:
        repaired = restore_manual_scopes(store, config["manual_items"])
        count, categories, sources, scopes = _first_pass(store, all_ids, suppressions)
        root = copy.deepcopy(baseline["manifest"])
        for key, actual in (("item_count", count), ("counts_by_category", dict(categories)),
                            ("counts_by_source", dict(sources)), ("counts_by_scope", dict(scopes))):
            if root.get(key) != actual:
                raise ValueError(f"Catalog snapshot differs from published baseline: {key}; nothing published")
        checked_at = baseline["source-health"]["generated_at"]
        root.update({"_output_dir": str(public), "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                     "rebuild_only": True, "collection_generated_at": root.get("collection_generated_at", root["generated_at"]),
                     "source_health_checked_at": checked_at, "rebuilt_from_release": baseline["current"]["release_id"]})
        build_search_v4_stream(store, all_ids, suppressions, copy.deepcopy(baseline["sources"]), root,
                               settings["output"], minimum_items=int(policy["governance"]["minimumItems"]))
    # Keep source observations verbatim; rebuilding an index is not a health check.
    health = copy.deepcopy(baseline["source-health"])
    health["disabled_sources"] = disabled
    _write_json(public / "data/source-health.json", health, pretty=True)
    status_path = public / "data/status.json"
    status = json.loads(status_path.read_text(encoding="utf-8"))
    status.update({"disabled_source_count": len(disabled), "source_health_checked_at": checked_at,
                   "collection_generated_at": root["collection_generated_at"], "rebuild_only": True})
    _write_json(status_path, status, pretty=True)
    validate_candidate(public)
    if fetch_json("data/v4/current.json") != baseline["current"]:
        raise ValueError("Live release changed while rebuilding; recheck before publication")
    print(json.dumps({"public_dir": str(public), "item_count": count, "counts_by_scope": dict(scopes),
                      "manual_scopes_restored_in_clone": repaired, "source_health_checked_at": checked_at,
                      "source_catalog_unchanged": True}))


if __name__ == "__main__":
    argparse.ArgumentParser(description=__doc__).parse_args()
    main()
