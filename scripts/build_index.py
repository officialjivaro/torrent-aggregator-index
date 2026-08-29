#!/usr/bin/env python3
"""Build the JTorrent static backend and publish the versioned v4 search contract."""
from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from jtorrent_backend.builder import build_index  # noqa: E402
from jtorrent_backend.config import load_config  # noqa: E402
from jtorrent_backend.search_policy import PolicyError, apply_policy, validate_policy_contract  # noqa: E402
from jtorrent_backend.search_v3 import build_search_v3  # noqa: E402
from jtorrent_backend.search_v4 import build_search_v4  # noqa: E402

DEFAULT_POLICY = ROOT / "config" / "search-policy.json"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build the JTorrent static JSON backend")
    parser.add_argument("--config", default="config/sources.yml", help="Path to the source configuration")
    parser.add_argument("--policy", default=str(DEFAULT_POLICY), help="Required public-search policy")
    parser.add_argument("--output-dir", default=None, help="Override the configured output directory")
    parser.add_argument("--offline", action="store_true", help="Skip network sources")
    parser.add_argument("--strict", action="store_true", help="Fail on any source fetch error")
    return parser.parse_args()


def read_required_policy(path: Path) -> dict:
    if not path.is_file():
        raise PolicyError(f"Required search policy was not found: {path}")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise PolicyError(f"Search policy is invalid JSON: {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise PolicyError("Search policy root must be an object.")
    return payload


def main() -> int:
    args = parse_args()
    source_config = load_config(Path(args.config))
    policy_path = Path(args.policy)
    try:
        policy = read_required_policy(policy_path)
        validate_policy_contract(source_config, policy)
        effective_config, disabled_records = apply_policy(source_config, policy)
    except PolicyError as exc:
        print(f"ERROR: Public-index policy rejected the build:\n{exc}", file=sys.stderr)
        return 2

    settings = effective_config.setdefault("settings", {})
    if args.output_dir:
        settings["output_dir"] = args.output_dir
    output_dir = Path(settings.get("output_dir", "public"))

    with tempfile.TemporaryDirectory(prefix="jtorrent-effective-config-") as temp_dir:
        effective_path = Path(temp_dir) / "sources.effective.yml"
        effective_path.write_text(
            yaml.safe_dump(effective_config, sort_keys=False, allow_unicode=True),
            encoding="utf-8",
        )
        manifest = build_index(effective_path, offline=args.offline, strict=args.strict)

    output_options = settings.get("output", {}) or {}
    if output_options.get("v4_only") is True:
        current_payload = json.loads((output_dir / "data" / "v4" / "current.json").read_text(encoding="utf-8"))
        v4_manifest = json.loads((output_dir / str(current_payload["manifest_path"])).read_text(encoding="utf-8"))
        v3 = {"enabled": False}
        v4 = {
            "enabled": True,
            "status": json.loads((output_dir / "data" / "status.json").read_text(encoding="utf-8")),
            "manifest": v4_manifest,
            "current": current_payload,
        }
    else:
        v3 = build_search_v3(output_dir, output_options)
        v4 = build_search_v4(output_dir, output_options)
    health_path = output_dir / "data" / "source-health.json"
    health_payload = json.loads(health_path.read_text(encoding="utf-8"))
    health_payload["disabled_sources"] = disabled_records
    health_path.write_text(json.dumps(health_payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    status_path = output_dir / "data" / "status.json"
    status_payload = json.loads(status_path.read_text(encoding="utf-8"))
    status_payload["disabled_source_count"] = len(disabled_records)
    status_path.write_text(json.dumps(status_payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    if v3.get("enabled"):
        v3["status"] = status_payload
    v4["status"] = status_payload

    root_manifest_path = output_dir / "data" / "manifest.json"
    root_payload = json.loads(root_manifest_path.read_text(encoding="utf-8"))
    root_payload["disabled_source_count"] = len(disabled_records)
    root_manifest_path.write_text(json.dumps(root_payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    status = v4.get("status", {}) if v4.get("enabled") else v3.get("status", {})
    print(
        f"Built {manifest.get('item_count', 0)} results into {output_dir}; "
        f"search contract {status.get('search_contract', 'v3')} with "
        f"{status.get('healthy_source_count', 0)} healthy sources and "
        f"{len(disabled_records)} policy-disabled sources"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
