#!/usr/bin/env python3
"""Audit JTorrent source governance and an optional generated backend artifact."""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from jtorrent_backend.config import load_config  # noqa: E402
from jtorrent_backend.search_policy import PolicyError, validate_policy_contract  # noqa: E402


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def size_tree(root: Path) -> int:
    return sum(path.stat().st_size for path in root.rglob("*") if path.is_file())


def generated_audit(public: Path, policy: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    errors: list[str] = []
    required = {
        "status": public / "data/status.json",
        "health": public / "data/source-health.json",
        "root_manifest": public / "data/manifest.json",
        "v4_current": public / "data/v4/current.json",
        "v3": public / "data/v3/manifest.json",
        "v2": public / "data/v2/manifest.json",
        "sources": public / "data/sources.json",
    }
    for label, path in required.items():
        if not path.is_file():
            errors.append(f"Missing generated {label}: {path}")
    if errors:
        return {}, errors

    status = read_json(required["status"])
    health = read_json(required["health"])
    root_manifest = read_json(required["root_manifest"])
    v4_current = read_json(required["v4_current"])
    v4_path = public / str(v4_current.get("manifest_path") or "")
    if not v4_path.is_file():
        return {}, [f"Missing generated v4 release manifest: {v4_path}"]
    v4 = read_json(v4_path)
    v3 = read_json(required["v3"])
    v2 = read_json(required["v2"])
    public_sources = read_json(required["sources"])
    governance = policy.get("governance") or {}
    disabled = policy.get("disabledSources") or {}
    disabled_ids = sorted(disabled)

    minimum_items = int(governance.get("minimumItems", 30_000))
    minimum_healthy = int(governance.get("minimumHealthySources", 1))
    maximum_failed = int(governance.get("maximumFailedSources", 0))
    maximum_bytes = int(governance.get("maximumArtifactBytes", 70 * 1024 * 1024))

    if status.get("search_contract") != "v4": errors.append("status.json must advertise search contract v4.")
    if int(status.get("item_count", 0)) < minimum_items: errors.append(f"Item count is below {minimum_items}.")
    if int(status.get("healthy_source_count", 0)) < minimum_healthy: errors.append(f"Healthy source count is below {minimum_healthy}.")
    if int(status.get("failed_source_count", 0)) > maximum_failed: errors.append(f"Failed source count exceeds {maximum_failed}.")
    if int(status.get("disabled_source_count", -1)) != len(disabled_ids): errors.append("Status disabled-source count differs from policy.")
    if root_manifest.get("search_contract") != "v4": errors.append("Root manifest does not advertise v4.")
    if v4.get("version") != 4: errors.append("v4 manifest has the wrong version.")
    if v4.get("posting_format") != ["doc_id", "category_index", "source_index", "scope_index", "static_score"]: errors.append("v4 posting format is invalid.")
    if v4.get("default_scope") != "open-authorized": errors.append("v4 default search scope is invalid.")
    if v3.get("version") != 3: errors.append("v3 manifest has the wrong version.")
    if v2.get("version") != 2 or v2.get("type") != "v3-compatibility-token-index": errors.append("v2 compatibility manifest is invalid.")
    if v2.get("doc_shards") != v3.get("doc_shards"): errors.append("v2 does not reuse the compact v3 document shards.")
    if v3.get("posting_format") != ["doc_id", "category_index", "source_index", "static_score"]: errors.append("v3 posting format is invalid.")

    health_disabled = sorted(str(item.get("id")) for item in health.get("disabled_sources", []) if item.get("id"))
    if health_disabled != disabled_ids: errors.append("Generated disabled-source inventory differs from policy.")
    health_sources = health.get("sources") or []
    active_health_ids = {
        str(item.get("id"))
        for item in health_sources
        if item.get("id") and str(item.get("state") or "").lower() not in {"disabled", "disabled-by-policy"}
    }
    leaked = sorted(active_health_ids & set(disabled_ids))
    if leaked: errors.append("Policy-disabled sources appear active: " + ", ".join(leaked))

    if isinstance(public_sources, dict):
        source_rows = public_sources.get("sources") or []
    else:
        source_rows = public_sources if isinstance(public_sources, list) else []
    public_source_ids = {str(item.get("id")) for item in source_rows if isinstance(item, dict) and item.get("id")}
    leaked_public = sorted(public_source_ids & set(disabled_ids))
    if leaked_public: errors.append("Policy-disabled sources appear in public source metadata: " + ", ".join(leaked_public))

    total_bytes = size_tree(public)
    if total_bytes > maximum_bytes: errors.append(f"Backend artifact is {total_bytes:,} bytes; budget is {maximum_bytes:,}.")

    for entry in [*v4.get("doc_shards", []), *v4.get("token_buckets", []), *v3.get("doc_shards", []), *v3.get("token_buckets", []), *v2.get("token_buckets", [])]:
        relative = str(entry.get("path") or "")
        path = public / relative
        if not relative or not path.is_file(): errors.append(f"Manifest references missing file: {relative!r}")
        elif path.stat().st_size != int(entry.get("bytes", -1)): errors.append(f"Manifest byte count is stale: {relative}")

    return {
        "item_count": int(status.get("item_count", 0)),
        "healthy_source_count": int(status.get("healthy_source_count", 0)),
        "failed_source_count": int(status.get("failed_source_count", 0)),
        "disabled_source_count": len(disabled_ids),
        "document_shards": len(v4.get("doc_shards", [])),
        "v4_token_bucket_parts": len(v4.get("token_buckets", [])),
        "v3_token_buckets": len(v3.get("token_buckets", [])),
        "v2_token_buckets": len(v2.get("token_buckets", [])),
        "artifact_bytes": total_bytes,
        "search_contract": status.get("search_contract"),
        "v2_compatibility": True,
    }, errors


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default="config/sources.yml")
    parser.add_argument("--policy", default="config/search-policy.json")
    parser.add_argument("--generated-dir")
    parser.add_argument("--output")
    parser.add_argument("--quiet", action="store_true")
    parser.add_argument("--generated-at", default=None)
    args = parser.parse_args()

    errors: list[str] = []
    try:
        config = load_config(Path(args.config))
    except Exception as exc:  # noqa: BLE001
        config = {}
        errors.append(f"Unable to load source configuration: {exc}")
    try:
        policy_path = Path(args.policy)
        if not policy_path.is_file():
            raise PolicyError(f"Required search policy was not found: {policy_path}")
        policy = read_json(policy_path)
        policy_report = validate_policy_contract(config, policy)
    except Exception as exc:  # noqa: BLE001
        policy = {}
        policy_report = {}
        errors.append(str(exc))

    generated_report: dict[str, Any] | None = None
    if args.generated_dir and policy:
        generated_report, generated_errors = generated_audit(Path(args.generated_dir), policy)
        errors.extend(generated_errors)

    report = {
        "schema_version": 1,
        "generated_at": args.generated_at or datetime.now(timezone.utc).isoformat(),
        "repository": "officialjivaro/torrent-aggregator-index",
        "policy": policy_report,
        "generated_backend": generated_report,
        "errors": errors,
        "passed": not errors,
    }
    if args.output:
        output = Path(args.output)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    if errors:
        print("\n".join(f"ERROR: {message}" for message in errors), file=sys.stderr)
        return 1
    if not args.quiet:
        message = f"Governance audit passed: {len(policy_report.get('active_source_ids', []))} active, {len(policy_report.get('disabled_source_ids', []))} policy-disabled sources"
        if generated_report:
            message += f"; {generated_report['item_count']:,} items, {generated_report['artifact_bytes']:,} bytes"
        print(message + ".")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
