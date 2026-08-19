from __future__ import annotations

import json
from pathlib import Path

from audit_governance import generated_audit


def write_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value) + "\n", encoding="utf-8")


def test_generated_audit_accepts_v3_with_v2_compatibility(tmp_path: Path):
    public = tmp_path / "public"
    doc = public / "data/v3/docs/docs-0001.json"
    token3 = public / "data/v3/tokens/ub.json"
    token2 = public / "data/v2/tokens/ub.json"
    write_json(doc, [{"doc_id": 0, "title": "Ubuntu ISO", "source_id": "ubuntu"}])
    write_json(token3, {"bucket": "ub", "tokens": {"ubuntu": [[0, 0, 0, 20]]}})
    write_json(token2, {"bucket": "ub", "tokens": {"ubuntu": [0]}})
    shards = [{"path": "data/v3/docs/docs-0001.json", "bytes": doc.stat().st_size, "count": 1}]
    v3_tokens = [{"path": "data/v3/tokens/ub.json", "bytes": token3.stat().st_size}]
    v2_tokens = [{"path": "data/v2/tokens/ub.json", "bytes": token2.stat().st_size}]
    write_json(public / "data/status.json", {"search_contract": "v3", "item_count": 1, "healthy_source_count": 1, "failed_source_count": 0, "disabled_source_count": 1})
    write_json(public / "data/source-health.json", {"sources": [{"id": "ubuntu", "state": "healthy"}], "disabled_sources": [{"id": "uncertain"}]})
    write_json(public / "data/manifest.json", {"search_contract": "v3"})
    write_json(public / "data/v3/manifest.json", {"version": 3, "item_count": 1, "posting_format": ["doc_id", "category_index", "source_index", "static_score"], "doc_shards": shards, "token_buckets": v3_tokens})
    write_json(public / "data/v2/manifest.json", {"version": 2, "type": "v3-compatibility-token-index", "doc_shards": shards, "token_buckets": v2_tokens})
    write_json(public / "data/sources.json", {"sources": [{"id": "ubuntu"}]})
    policy = {"disabledSources": {"uncertain": "reason"}, "governance": {"minimumItems": 1, "minimumHealthySources": 1, "maximumFailedSources": 0, "maximumArtifactBytes": 1000000}}
    report, errors = generated_audit(public, policy)
    assert errors == []
    assert report["search_contract"] == "v3"
    assert report["v2_compatibility"] is True


def test_generated_audit_rejects_policy_disabled_source_in_public_metadata(tmp_path: Path):
    public = tmp_path / "public"
    write_json(public / "data/status.json", {})
    write_json(public / "data/source-health.json", {"sources": [], "disabled_sources": []})
    write_json(public / "data/manifest.json", {})
    write_json(public / "data/v3/manifest.json", {"doc_shards": [], "token_buckets": []})
    write_json(public / "data/v2/manifest.json", {"doc_shards": [], "token_buckets": []})
    write_json(public / "data/sources.json", {"sources": [{"id": "uncertain"}]})
    policy = {"disabledSources": {"uncertain": "reason"}, "governance": {"minimumItems": 0, "minimumHealthySources": 0, "maximumFailedSources": 0, "maximumArtifactBytes": 1000000}}
    _, errors = generated_audit(public, policy)
    assert any("public source metadata" in error for error in errors)
