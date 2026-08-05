#!/usr/bin/env python3
"""Validate the generated static JTorrent backend before GitHub Pages deployment."""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

ROOT_REQUIRED_FILES = ["index.html", "robots.txt", "sitemap.xml"]
DATA_REQUIRED_FILES = [
    "manifest.json",
    "search-index.min.json",
    "search-index.summary.json",
    "search-index.json",
    "sources.json",
    "shards/index.json",
]


class ValidationError(RuntimeError):
    """Raised when generated backend data is missing, invalid, or unsafe to deploy."""


def fail(message: str) -> None:
    raise ValidationError(message)


def load_json(path: Path) -> Any:
    if not path.exists():
        fail(f"Missing required JSON file: {path}")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        fail(f"Invalid JSON in {path}: {exc}")


def require(condition: bool, message: str) -> None:
    if not condition:
        fail(message)


def parse_iso_datetime(value: Any, *, label: str) -> None:
    require(isinstance(value, str) and value.strip(), f"{label} is missing")
    normalized = value.replace("Z", "+00:00")
    try:
        datetime.fromisoformat(normalized)
    except ValueError as exc:
        fail(f"{label} is not ISO-8601: {value!r}")


def resolve_output_path(public_dir: Path, data_dir: Path, rel_path: Any) -> Path:
    value = str(rel_path or "").strip().replace("\\", "/")
    require(bool(value), "A manifest/shard entry has an empty path")
    if value.startswith("data/"):
        return public_dir / value
    return data_dir / value


def validate_json_file_reference(public_dir: Path, data_dir: Path, rel_path: Any, *, label: str) -> Any:
    path = resolve_output_path(public_dir, data_dir, rel_path)
    require(path.exists(), f"{label} points to a missing file: {path}")
    return load_json(path)


def validate_shard_list(
    public_dir: Path,
    data_dir: Path,
    shards: Any,
    *,
    label: str,
    expected_total: int | None = None,
) -> int:
    if shards in (None, []):
        return 0
    require(isinstance(shards, list), f"{label} must be a list")

    total = 0
    for index, shard in enumerate(shards, start=1):
        require(isinstance(shard, dict), f"{label}[{index}] must be an object")
        payload = validate_json_file_reference(public_dir, data_dir, shard.get("path"), label=f"{label}[{index}]")
        require(isinstance(payload, list), f"{label}[{index}] must point to a JSON array shard")

        expected_count = int(shard.get("count", -1))
        require(
            len(payload) == expected_count,
            f"{label}[{index}] count mismatch: got {len(payload)}, expected {expected_count}",
        )

        shard_path = resolve_output_path(public_dir, data_dir, shard.get("path"))
        declared_bytes = shard.get("bytes")
        if declared_bytes is not None:
            require(
                shard_path.stat().st_size == int(declared_bytes),
                f"{label}[{index}] byte mismatch: got {shard_path.stat().st_size}, expected {declared_bytes}",
            )
        total += len(payload)

    if expected_total is not None:
        require(total == expected_total, f"{label} contains {total} rows; expected {expected_total}")
    return total


def validate_index_payload(
    public_dir: Path,
    data_dir: Path,
    file_name: str,
    expected_count: int,
    shard_key: str,
) -> None:
    payload = load_json(data_dir / file_name)
    if isinstance(payload, list):
        require(
            len(payload) == expected_count,
            f"{file_name} has {len(payload)} rows; expected {expected_count}",
        )
        return

    require(isinstance(payload, dict), f"{file_name} must be a JSON array or an object")
    mode = payload.get("mode")
    if mode == "v2-only":
        require(
            bool(payload.get("search_v2_manifest_path")),
            f"{file_name} v2-only pointer is missing search_v2_manifest_path",
        )
        validate_json_file_reference(
            public_dir,
            data_dir,
            payload.get("search_v2_manifest_path"),
            label=f"{file_name}.search_v2_manifest_path",
        )
        return

    require(mode == "sharded", f"{file_name} object payload must have mode='sharded' or mode='v2-only'")
    validate_shard_list(
        public_dir,
        data_dir,
        payload.get(shard_key),
        label=f"{file_name}.{shard_key}",
        expected_total=expected_count,
    )


def validate_group_manifest(public_dir: Path, data_dir: Path, groups: Any, *, label: str) -> None:
    if not groups:
        return
    require(isinstance(groups, dict), f"{label} must be an object")
    for key, entry in groups.items():
        require(isinstance(entry, dict), f"{label}.{key} must be an object")
        payload = validate_json_file_reference(public_dir, data_dir, entry.get("path"), label=f"{label}.{key}")
        if entry.get("mode") == "inline":
            require(isinstance(payload, list), f"{label}.{key} inline payload must be a JSON array")
        elif entry.get("mode") == "sharded":
            require(
                isinstance(payload, dict) and payload.get("mode") == "sharded",
                f"{label}.{key} sharded payload is invalid",
            )
            validate_shard_list(public_dir, data_dir, entry.get("parts"), label=f"{label}.{key}.parts")
        else:
            fail(f"{label}.{key} has unknown mode: {entry.get('mode')!r}")


def load_sources(public_dir: Path, data_dir: Path, *, fail_on_optional_source_errors: bool = False) -> list[dict[str, Any]]:
    payload = load_json(data_dir / "sources.json")
    if isinstance(payload, list):
        sources = payload
    else:
        require(
            isinstance(payload, dict) and payload.get("mode") == "sharded",
            "sources.json must be a list or sharded object",
        )
        sources = []
        for shard in payload.get("source_shards", []) or []:
            shard_payload = validate_json_file_reference(
                public_dir,
                data_dir,
                shard.get("path"),
                label="sources.json.source_shards",
            )
            require(isinstance(shard_payload, list), "sources shard must be a JSON array")
            sources.extend(shard_payload)

    seen: set[str] = set()
    source_errors: list[str] = []
    min_item_errors: list[str] = []
    for source in sources:
        require(isinstance(source, dict), "sources.json entries must be objects")
        sid = source.get("id")
        require(isinstance(sid, str) and sid, "sources.json entry is missing id")
        require(sid not in seen, f"Duplicate source id in sources.json: {sid}")
        seen.add(sid)

        required = source.get("required") is not False
        error = source.get("error")
        # Offline builds intentionally mark skipped network sources this way.
        # Optional best-effort sources can be flaky; their errors are kept in
        # sources.json but do not block deploy unless explicitly requested.
        offline_skipped = bool(error and str(error).strip().lower() == "offline mode")
        if error and not offline_skipped:
            if required or fail_on_optional_source_errors:
                source_errors.append(f"{sid}: {error}")

        min_items = int(source.get("min_items") or 0)
        raw_count = int(source.get("item_count") or 0)
        if required and min_items and raw_count < min_items and not offline_skipped:
            min_item_errors.append(f"{sid}: item_count={raw_count}, expected at least {min_items}")

        indexed = source.get("indexed_item_count")
        if indexed is not None:
            indexed_count = int(indexed or 0)
            require(indexed_count >= 0, f"{sid}: indexed_item_count must be non-negative")
            require(raw_count >= 0, f"{sid}: item_count must be non-negative")

    require(not source_errors, "sources.json contains blocking source errors: " + "; ".join(source_errors))
    require(not min_item_errors, "sources.json has required sources below min_items: " + "; ".join(min_item_errors))
    return sources

def validate_search_v2(public_dir: Path, data_dir: Path, manifest: dict[str, Any], *, expected_count: int) -> None:
    search_v2 = manifest.get("search_v2")
    if not search_v2:
        return
    require(isinstance(search_v2, dict), "manifest.search_v2 must be an object")
    v2_payload = validate_json_file_reference(public_dir, data_dir, "v2/manifest.json", label="search_v2.manifest")
    require(isinstance(v2_payload, dict), "data/v2/manifest.json must contain an object")
    require(v2_payload.get("version") == 2, "data/v2/manifest.json version must be 2")
    require(int(v2_payload.get("doc_count") or 0) == expected_count, "search_v2 doc_count must equal manifest.item_count")

    doc_total = validate_shard_list(
        public_dir,
        data_dir,
        v2_payload.get("doc_shards"),
        label="search_v2.doc_shards",
        expected_total=expected_count,
    )
    require(doc_total == expected_count, "search_v2 doc shards do not cover all documents")

    buckets = v2_payload.get("token_buckets") or []
    require(isinstance(buckets, list), "search_v2.token_buckets must be a list")
    require(buckets, "search_v2.token_buckets must not be empty")
    for index, bucket in enumerate(buckets, start=1):
        require(isinstance(bucket, dict), f"search_v2.token_buckets[{index}] must be an object")
        payload = validate_json_file_reference(
            public_dir,
            data_dir,
            bucket.get("path"),
            label=f"search_v2.token_buckets[{index}]",
        )
        require(isinstance(payload, dict), f"search_v2 token bucket {index} must contain an object")
        require(isinstance(payload.get("tokens"), dict), f"search_v2 token bucket {index} is missing tokens object")

def append_step_summary(report: dict[str, Any]) -> None:
    summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
    if not summary_path:
        return
    lines = [
        "## JTorrent backend QC",
        "",
        "Generated backend validation passed.",
        "",
        f"- Generated: `{report.get('generated_at')}`",
        f"- Items: `{report.get('item_count')}`",
        f"- Raw items: `{report.get('raw_item_count')}`",
        f"- Enabled sources: `{report.get('enabled_source_count')}`",
        f"- Source summaries: `{report.get('source_summary_count')}`",
    ]
    warnings = report.get("warnings") or []
    if warnings:
        lines.extend(["", "Warnings:"])
        lines.extend(f"- {warning}" for warning in warnings)
    with open(summary_path, "a", encoding="utf-8") as handle:
        handle.write("\n".join(lines) + "\n")


def validate_public_backend(
    public_dir: Path,
    *,
    min_items: int = 1,
    fail_on_warnings: bool = False,
    require_nojekyll: bool = False,
    fail_on_optional_source_errors: bool = False,
) -> dict[str, Any]:
    public_dir = public_dir.resolve()
    data_dir = public_dir / "data"

    require(public_dir.exists(), f"Public directory does not exist: {public_dir}")
    require(data_dir.exists(), f"Data directory does not exist: {data_dir}")

    for file_name in ROOT_REQUIRED_FILES:
        require((public_dir / file_name).exists(), f"Missing generated public file: {public_dir / file_name}")
    if require_nojekyll:
        require((public_dir / ".nojekyll").exists(), "Missing public/.nojekyll")

    for rel in DATA_REQUIRED_FILES:
        load_json(data_dir / rel)

    # Parse every generated JSON file so shard and grouped files are covered too.
    for path in sorted(data_dir.rglob("*.json")):
        load_json(path)

    manifest = load_json(data_dir / "manifest.json")
    require(isinstance(manifest, dict), "manifest.json must contain an object")
    parse_iso_datetime(manifest.get("generated_at"), label="manifest.generated_at")

    item_count = int(manifest.get("item_count", 0) or 0)
    raw_item_count = int(manifest.get("raw_item_count", 0) or 0)
    enabled_source_count = int(manifest.get("enabled_source_count", 0) or 0)
    errors = manifest.get("errors") or []
    warnings = manifest.get("warnings") or []

    require(item_count >= min_items, f"manifest.item_count={item_count}; expected at least {min_items}")
    require(
        raw_item_count >= item_count,
        "manifest.raw_item_count must be greater than or equal to manifest.item_count",
    )
    require(enabled_source_count > 0, "manifest.enabled_source_count must be greater than zero")
    require(not errors, "manifest.errors is not empty: " + "; ".join(str(error) for error in errors))
    if fail_on_warnings:
        require(not warnings, "manifest.warnings is not empty: " + "; ".join(str(warning) for warning in warnings))

    files = manifest.get("files") or {}
    require(isinstance(files, dict), "manifest.files must be an object")
    for key, entry in files.items():
        require(isinstance(entry, dict), f"manifest.files.{key} must be an object")
        validate_json_file_reference(public_dir, data_dir, entry.get("path"), label=f"manifest.files.{key}")

    validate_index_payload(public_dir, data_dir, "search-index.json", item_count, "full_shards")
    validate_index_payload(public_dir, data_dir, "search-index.min.json", item_count, "compact_shards")
    validate_index_payload(public_dir, data_dir, "search-index.summary.json", item_count, "search_shards")
    validate_search_v2(public_dir, data_dir, manifest, expected_count=item_count)

    sources = load_sources(public_dir, data_dir, fail_on_optional_source_errors=fail_on_optional_source_errors)

    shard_index = load_json(data_dir / "shards" / "index.json")
    require(isinstance(shard_index, dict), "data/shards/index.json must contain an object")

    sharding = manifest.get("sharding") or {}
    require(isinstance(sharding, dict), "manifest.sharding must be an object")
    if sharding:
        legacy_active = sharding.get("legacy_active") is not False
        validate_shard_list(
            public_dir,
            data_dir,
            sharding.get("full_shards"),
            label="manifest.sharding.full_shards",
            expected_total=item_count if legacy_active else None,
        )
        validate_shard_list(
            public_dir,
            data_dir,
            sharding.get("compact_shards"),
            label="manifest.sharding.compact_shards",
            expected_total=item_count if legacy_active else None,
        )
        validate_shard_list(
            public_dir,
            data_dir,
            sharding.get("search_shards"),
            label="manifest.sharding.search_shards",
            expected_total=item_count if legacy_active else None,
        )
        validate_shard_list(
            public_dir,
            data_dir,
            sharding.get("source_shards"),
            label="manifest.sharding.source_shards",
        )
        validate_group_manifest(
            public_dir,
            data_dir,
            sharding.get("by_category"),
            label="manifest.sharding.by_category",
        )
        validate_group_manifest(
            public_dir,
            data_dir,
            sharding.get("by_source"),
            label="manifest.sharding.by_source",
        )

    report = {
        "generated_at": manifest.get("generated_at"),
        "item_count": item_count,
        "raw_item_count": raw_item_count,
        "enabled_source_count": enabled_source_count,
        "source_summary_count": len(sources),
        "warnings": warnings,
    }
    append_step_summary(report)
    return report


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Validate generated JTorrent public JSON backend")
    parser.add_argument("--public-dir", default="public", help="Generated public output directory")
    parser.add_argument("--min-items", type=int, default=1, help="Minimum acceptable manifest.item_count")
    parser.add_argument("--fail-on-warnings", action="store_true", help="Fail if manifest.warnings is non-empty")
    parser.add_argument("--require-nojekyll", action="store_true", help="Require public/.nojekyll to exist")
    parser.add_argument("--fail-on-optional-source-errors", action="store_true", help="Fail if any optional/best-effort source has an error")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        report = validate_public_backend(
            Path(args.public_dir),
            min_items=args.min_items,
            fail_on_warnings=args.fail_on_warnings,
            require_nojekyll=args.require_nojekyll,
            fail_on_optional_source_errors=args.fail_on_optional_source_errors,
        )
    except ValidationError as exc:
        print(f"Backend QC failed: {exc}", file=sys.stderr)
        return 1

    print("Backend QC passed")
    print(f"Generated: {report['generated_at']}")
    print(f"Items: {report['item_count']}")
    print(f"Raw items: {report['raw_item_count']}")
    print(f"Enabled sources: {report['enabled_source_count']}")
    print(f"Source summaries: {report['source_summary_count']}")
    if report["warnings"]:
        print("Warnings:")
        for warning in report["warnings"]:
            print(f"- {warning}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
