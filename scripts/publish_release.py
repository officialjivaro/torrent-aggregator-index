#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import mimetypes
import os
import shutil
import sys
import zipfile
from collections import defaultdict
from pathlib import Path
from typing import Any, Iterable

import boto3
import yaml


ROOT = Path(__file__).resolve().parents[1]


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def load_settings() -> dict[str, Any]:
    payload = yaml.safe_load((ROOT / "config" / "sources.yml").read_text(encoding="utf-8")) or {}
    return payload.get("settings", {}) or {}


def public_files(public_dir: Path) -> list[Path]:
    return sorted(path for path in public_dir.rglob("*") if path.is_file())


def prepare(public_dir: Path, backup_dir: Path, manifest_path: Path) -> dict[str, Any]:
    current = read_json(public_dir / "data" / "v4" / "current.json")
    release_id = str(current["release_id"])
    publishing = load_settings().get("publishing", {}) or {}
    asset_limit = int(publishing.get("github_asset_soft_limit_bytes", 1_932_735_283))
    bundle_target = min(asset_limit, 1_718_000_000)
    files = public_files(public_dir)
    if any(path.stat().st_size > bundle_target for path in files):
        oversized = next(path for path in files if path.stat().st_size > bundle_target)
        raise RuntimeError(f"A generated file is too large for a safe GitHub backup bundle: {oversized}")

    release_dir = backup_dir / release_id
    shutil.rmtree(release_dir, ignore_errors=True)
    release_dir.mkdir(parents=True, exist_ok=True)
    groups: list[list[Path]] = []
    current_group: list[Path] = []
    current_size = 0
    for path in files:
        size = path.stat().st_size
        if current_group and current_size + size > bundle_target:
            groups.append(current_group)
            current_group = []
            current_size = 0
        current_group.append(path)
        current_size += size
    if current_group:
        groups.append(current_group)

    assets: list[dict[str, Any]] = []
    for number, group in enumerate(groups, 1):
        target = release_dir / f"jtorrent-index-{release_id}-part-{number:03d}.zip"
        with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6, allowZip64=True) as archive:
            for path in group:
                archive.write(path, path.relative_to(public_dir).as_posix())
        if target.stat().st_size > asset_limit:
            raise RuntimeError(f"GitHub backup asset exceeds configured safety limit: {target}")
        assets.append({"path": str(target.resolve()), "bytes": target.stat().st_size})

    release_manifest = {
        "release_id": release_id,
        "tag": f"index-{release_id}",
        "generated_at": current.get("generated_at"),
        "item_count": current.get("item_count"),
        "public_bytes": sum(path.stat().st_size for path in files),
        "assets": assets,
    }
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(json.dumps(release_manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    keep = max(1, int(publishing.get("local_backup_count", 2)))
    releases = sorted((path for path in backup_dir.iterdir() if path.is_dir()), key=lambda path: path.stat().st_mtime, reverse=True)
    for stale in releases[keep:]:
        shutil.rmtree(stale)
    print(json.dumps(release_manifest, separators=(",", ":")))
    return release_manifest


def r2_client():
    required = ["CLOUDFLARE_ACCOUNT_ID", "R2_ACCESS_KEY_ID", "R2_SECRET_ACCESS_KEY"]
    missing = [name for name in required if not os.environ.get(name)]
    if missing:
        raise RuntimeError("Missing R2 environment variables: " + ", ".join(missing))
    return boto3.client(
        "s3",
        endpoint_url=f"https://{os.environ['CLOUDFLARE_ACCOUNT_ID']}.r2.cloudflarestorage.com",
        aws_access_key_id=os.environ["R2_ACCESS_KEY_ID"],
        aws_secret_access_key=os.environ["R2_SECRET_ACCESS_KEY"],
        region_name="auto",
    )


def list_objects(client, bucket: str) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    token: str | None = None
    while True:
        kwargs = {"Bucket": bucket}
        if token:
            kwargs["ContinuationToken"] = token
        page = client.list_objects_v2(**kwargs)
        output.extend(page.get("Contents", []))
        if not page.get("IsTruncated"):
            return output
        token = page.get("NextContinuationToken")


def delete_keys(client, bucket: str, keys: Iterable[str]) -> None:
    rows = list(keys)
    for offset in range(0, len(rows), 1000):
        batch = rows[offset : offset + 1000]
        client.delete_objects(Bucket=bucket, Delete={"Objects": [{"Key": key} for key in batch], "Quiet": True})


def object_args(path: Path, key: str) -> dict[str, str]:
    content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    mutable = key in {"data/v4/current.json", "data/status.json", "data/manifest.json", "index.html"}
    return {
        "ContentType": content_type,
        "CacheControl": "no-cache, max-age=0, must-revalidate" if mutable else "public, max-age=31536000, immutable",
    }


def publish_r2(public_dir: Path) -> dict[str, Any]:
    settings = load_settings()
    publishing = settings.get("publishing", {}) or {}
    bucket = str(publishing.get("r2_bucket") or "").strip()
    if not bucket:
        raise RuntimeError("settings.publishing.r2_bucket is required")
    soft_limit = int(publishing.get("r2_soft_limit_bytes", 8_589_934_592))
    keep_versions = max(1, int(publishing.get("keep_r2_versions", 3)))
    current = read_json(public_dir / "data" / "v4" / "current.json")
    release_id = str(current["release_id"])
    client = r2_client()
    if bool(publishing.get("r2_manage_cors", False)):
        client.put_bucket_cors(Bucket=bucket, CORSConfiguration={"CORSRules": [{
            "AllowedMethods": ["GET", "HEAD"],
            "AllowedOrigins": ["https://www.jtorrent.net", "https://jtorrent.net"],
            "AllowedHeaders": ["*"],
            "ExposeHeaders": ["ETag"],
            "MaxAgeSeconds": 86400,
        }]})

    files = public_files(public_dir)
    planned = {path.relative_to(public_dir).as_posix(): path.stat().st_size for path in files}
    existing = list_objects(client, bucket)
    existing_sizes = {str(row["Key"]): int(row.get("Size", 0)) for row in existing}
    projected = sum(existing_sizes.values()) + sum(size - existing_sizes.get(key, 0) for key, size in planned.items())

    by_release: dict[str, list[str]] = defaultdict(list)
    for key in existing_sizes:
        parts = key.split("/")
        if len(parts) > 4 and parts[:3] == ["data", "v4", "releases"]:
            by_release[parts[3]].append(key)
    stale_release_ids = sorted(value for value in by_release if value != release_id)
    allowed_stale_releases = max(0, keep_versions - 1)
    for stale_id in stale_release_ids:
        remaining_stale = sum(1 for value in by_release if value != release_id)
        if projected <= soft_limit and remaining_stale <= allowed_stale_releases:
            break
        keys = by_release.pop(stale_id)
        delete_keys(client, bucket, keys)
        projected -= sum(existing_sizes[key] for key in keys)
    if projected > soft_limit:
        raise RuntimeError(f"R2 publish would use {projected} bytes, above the configured soft limit of {soft_limit} bytes")

    pointer_key = "data/v4/current.json"
    release_prefix = f"data/v4/releases/{release_id}/"
    ordered = sorted(planned, key=lambda key: (0 if key.startswith(release_prefix) else 1, key == pointer_key, key))
    uploaded = 0
    for key in ordered:
        if key == pointer_key:
            continue
        path = public_dir / Path(key)
        client.upload_file(str(path), bucket, key, ExtraArgs=object_args(path, key))
        uploaded += 1
    pointer_path = public_dir / Path(pointer_key)
    client.upload_file(str(pointer_path), bucket, pointer_key, ExtraArgs=object_args(pointer_path, pointer_key))
    uploaded += 1

    refreshed = list_objects(client, bucket)
    refreshed_releases: dict[str, list[str]] = defaultdict(list)
    for row in refreshed:
        parts = str(row["Key"]).split("/")
        if len(parts) > 4 and parts[:3] == ["data", "v4", "releases"]:
            refreshed_releases[parts[3]].append(str(row["Key"]))
    stale_ids = [value for value in sorted(refreshed_releases) if value != release_id]
    while len(refreshed_releases) > keep_versions and stale_ids:
        stale_id = stale_ids.pop(0)
        delete_keys(client, bucket, refreshed_releases.pop(stale_id))

    result = {"release_id": release_id, "bucket": bucket, "uploaded_files": uploaded, "projected_bytes": projected}
    print(json.dumps(result, separators=(",", ":")))
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="Prepare GitHub backup assets or publish the current index to R2")
    subparsers = parser.add_subparsers(dest="command", required=True)
    prepare_parser = subparsers.add_parser("prepare")
    prepare_parser.add_argument("--public-dir", default="public")
    prepare_parser.add_argument("--backup-dir", default="backups")
    prepare_parser.add_argument("--manifest", default="staging/release.json")
    r2_parser = subparsers.add_parser("r2")
    r2_parser.add_argument("--public-dir", default="public")
    args = parser.parse_args()
    try:
        if args.command == "prepare":
            prepare(Path(args.public_dir), Path(args.backup_dir), Path(args.manifest))
        else:
            publish_r2(Path(args.public_dir))
    except Exception as exc:  # noqa: BLE001
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
