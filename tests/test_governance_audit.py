from __future__ import annotations

import json
from pathlib import Path

from audit_governance import generated_audit


def write_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value) + "\n", encoding="utf-8")


def test_generated_audit_accepts_v4_with_v3_fallback(tmp_path: Path):
    public = tmp_path / "public"
    doc = public / "data/v3/docs/docs-0001.json"
    token3 = public / "data/v3/tokens/ub.json"
    token2 = public / "data/v2/tokens/ub.json"
    doc4 = public / "data/v4/releases/v4-test/docs/docs-00001.json"
    token4 = public / "data/v4/releases/v4-test/tokens/ub-001.json"
    write_json(doc, [{"doc_id": 0, "title": "Ubuntu ISO", "source_id": "ubuntu"}])
    write_json(token3, {"bucket": "ub", "tokens": {"ubuntu": [[0, 0, 0, 20]]}})
    write_json(token2, {"bucket": "ub", "tokens": {"ubuntu": [0]}})
    write_json(doc4, [{"doc_id": 0, "title": "Ubuntu ISO", "source_id": "ubuntu", "policy_scope": "open-authorized"}])
    write_json(token4, {"bucket": "ub", "tokens": {"ubuntu": {"count": 1, "postings": [[0, 0, 0, 0, 20]]}}})
    shards = [{"path": "data/v3/docs/docs-0001.json", "bytes": doc.stat().st_size, "count": 1}]
    v3_tokens = [{"path": "data/v3/tokens/ub.json", "bytes": token3.stat().st_size}]
    v2_tokens = [{"path": "data/v2/tokens/ub.json", "bytes": token2.stat().st_size}]
    v4_shards = [{"path": "data/v4/releases/v4-test/docs/docs-00001.json", "bytes": doc4.stat().st_size, "count": 1}]
    v4_tokens = [{"path": "data/v4/releases/v4-test/tokens/ub-001.json", "bytes": token4.stat().st_size}]
    write_json(public / "data/status.json", {"search_contract": "v4", "item_count": 1, "healthy_source_count": 1, "failed_source_count": 1, "disabled_source_count": 1})
    write_json(public / "data/source-health.json", {"sources": [{"id": "ubuntu", "state": "healthy", "required": True}, {"id": "optional", "state": "failed", "required": False}], "disabled_sources": [{"id": "uncertain"}]})
    write_json(public / "data/manifest.json", {"search_contract": "v4"})
    write_json(public / "data/v4/current.json", {"version": 4, "release_id": "v4-test", "manifest_path": "data/v4/releases/v4-test/manifest.json"})
    write_json(public / "data/v4/releases/v4-test/manifest.json", {"version": 4, "release_id": "v4-test", "item_count": 1, "default_scope": "open-authorized", "posting_format": ["doc_id", "category_index", "source_index", "scope_index", "static_score"], "doc_shards": v4_shards, "token_buckets": v4_tokens})
    write_json(public / "data/v3/manifest.json", {"version": 3, "item_count": 1, "posting_format": ["doc_id", "category_index", "source_index", "static_score"], "doc_shards": shards, "token_buckets": v3_tokens})
    write_json(public / "data/v2/manifest.json", {"version": 2, "type": "v3-compatibility-token-index", "doc_shards": shards, "token_buckets": v2_tokens})
    write_json(public / "data/sources.json", {"sources": [{"id": "ubuntu"}]})
    policy = {"disabledSources": {"uncertain": "reason"}, "governance": {"minimumItems": 1, "minimumHealthySources": 1, "maximumFailedRequiredSources": 0, "maximumFailedOptionalSources": 1, "maximumArtifactBytes": 1000000}}
    report, errors = generated_audit(public, policy)
    assert errors == []
    assert report["search_contract"] == "v4"
    assert report["v2_compatibility"] is True
    assert report["failed_required_source_count"] == 0
    assert report["failed_optional_source_count"] == 1

    write_json(public / "data/source-health.json", {"sources": [{"id": "ubuntu", "state": "healthy", "required": True}, {"id": "required", "state": "failed", "required": True}], "disabled_sources": [{"id": "uncertain"}]})
    _, errors = generated_audit(public, policy)
    assert any("Required failed source count" in error for error in errors)


def test_generated_audit_rejects_policy_disabled_source_in_public_metadata(tmp_path: Path):
    public = tmp_path / "public"
    write_json(public / "data/status.json", {})
    write_json(public / "data/source-health.json", {"sources": [], "disabled_sources": []})
    write_json(public / "data/manifest.json", {})
    write_json(public / "data/v4/current.json", {"manifest_path": "data/v4/releases/v4-test/manifest.json"})
    write_json(public / "data/v4/releases/v4-test/manifest.json", {"doc_shards": [], "token_buckets": []})
    write_json(public / "data/v3/manifest.json", {"doc_shards": [], "token_buckets": []})
    write_json(public / "data/v2/manifest.json", {"doc_shards": [], "token_buckets": []})
    write_json(public / "data/sources.json", {"sources": [{"id": "uncertain"}]})
    policy = {"disabledSources": {"uncertain": "reason"}, "governance": {"minimumItems": 0, "minimumHealthySources": 0, "maximumFailedSources": 0, "maximumArtifactBytes": 1000000}}
    _, errors = generated_audit(public, policy)
    assert any("public source metadata" in error for error in errors)
