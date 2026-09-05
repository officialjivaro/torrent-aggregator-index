#!/usr/bin/env python3
from __future__ import annotations

import argparse
import base64
import hashlib
import re
import subprocess
from botocore.exceptions import ClientError
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
    if not re.fullmatch(r"v4-[A-Za-z0-9-]+", release_id):
        raise RuntimeError("Unsafe release identifier")
    publishing = load_settings().get("publishing", {}) or {}
    asset_limit = int(publishing.get("github_asset_soft_limit_bytes", 1_932_735_283))
    bundle_target = min(asset_limit, 1_718_000_000)
    files = public_files(public_dir)
    if any(path.stat().st_size > bundle_target for path in files):
        oversized = next(path for path in files if path.stat().st_size > bundle_target)
        raise RuntimeError(f"A generated file is too large for a safe GitHub backup bundle: {oversized}")

    release_dir = backup_dir / release_id
    if release_dir.exists():
        raise RuntimeError("Backup release already exists; use a new immutable release ID")
    release_dir.mkdir(parents=True)
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

    # Keep existing local backups until the replacement is verified live.
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
    mutable = not key.startswith("data/v4/releases/")
    return {
        "ContentType": content_type,
        "CacheControl": "no-cache, max-age=0, must-revalidate" if mutable else "public, max-age=31536000, immutable",
    }


def _remote_bytes(client, bucket: str, key: str) -> bytes | None:
    try:
        response = client.get_object(Bucket=bucket, Key=key)
    except ClientError as exc:
        if str(exc.response.get("Error", {}).get("Code")) in {"NoSuchKey", "404", "NotFound"}:
            return None
        raise
    try:
        return response["Body"].read()
    finally:
        response["Body"].close()


def validate_candidate(public_dir: Path) -> None:
    settings = load_settings()
    minimum = int((settings.get("limits") or {}).get("min_total_items", 30000))
    maximum = int((settings.get("publishing") or {}).get("r2_soft_limit_bytes", 64 * 1024**3))
    subprocess.run([sys.executable, str(ROOT / "scripts/validate_v4_backend.py"),
                    "--public-dir", str(public_dir), "--minimum-items", str(minimum),
                    "--max-bytes", str(maximum)], check=True)
    subprocess.run([sys.executable, str(ROOT / "scripts/audit_governance.py"),
                    "--config", str(ROOT / "config/sources.yml"),
                    "--policy", str(ROOT / "config/search-policy.json"),
                    "--generated-dir", str(public_dir)], check=True)


def publish_r2(public_dir: Path) -> dict[str, Any]:
    public_dir = public_dir.resolve()
    validate_candidate(public_dir)
    settings = load_settings()
    publishing = settings.get("publishing", {}) or {}
    bucket = str(publishing.get("r2_bucket") or "").strip()
    if not bucket:
        raise RuntimeError("settings.publishing.r2_bucket is required")
    soft_limit = int(publishing.get("r2_soft_limit_bytes", 8_589_934_592))
    keep_versions = max(2, int(publishing.get("keep_r2_versions", 3)))
    pointer_key = "data/v4/current.json"
    current = read_json(public_dir / pointer_key)
    release_id = str(current["release_id"])
    if not re.fullmatch(r"v4-[A-Za-z0-9-]+", release_id):
        raise RuntimeError("Unsafe release identifier")
    release_prefix = f"data/v4/releases/{release_id}/"
    client = r2_client()
    files = public_files(public_dir)
    if any(path.is_symlink() or not path.resolve().is_relative_to(public_dir) for path in files):
        raise RuntimeError("Publication contains a symlink or escaped path")
    planned = {path.relative_to(public_dir).as_posix(): path.stat().st_size for path in files}
    if any(key.startswith("data/v4/releases/") and not key.startswith(release_prefix) for key in planned):
        raise RuntimeError("Candidate contains files belonging to another immutable release")
    existing = list_objects(client, bucket)
    existing_sizes = {str(row["Key"]): int(row.get("Size", 0)) for row in existing}
    projected = sum(existing_sizes.values()) + sum(size - existing_sizes.get(key, 0) for key, size in planned.items())
    # Never free capacity by deleting the index that is still serving visitors.
    if projected > soft_limit:
        raise RuntimeError(f"R2 publish needs {projected} bytes, above the {soft_limit} byte limit; nothing changed")

    old_pointer = _remote_bytes(client, bucket, pointer_key)
    old_release = json.loads(old_pointer)["release_id"] if old_pointer is not None else None
    immutable = sorted(key for key in planned if key.startswith(release_prefix))
    unexpected = [key for key in existing_sizes if key.startswith(release_prefix) and key not in planned]
    if unexpected:
        raise RuntimeError("Existing immutable release contains unexpected files; use a new release ID")
    digests: dict[str, str] = {}
    for key in planned:
        with (public_dir / key).open("rb") as handle:
            digests[key] = hashlib.file_digest(handle, "sha256").hexdigest()
    # Preflight ALL collisions before uploading even one object.
    for key in immutable:
        if key in existing_sizes:
            head = client.head_object(Bucket=bucket, Key=key)
            digest = head.get("Metadata", {}).get("sha256")
            if not digest:
                digest = hashlib.sha256(_remote_bytes(client, bucket, key) or b"").hexdigest()
            if existing_sizes[key] != planned[key] or digest != digests[key]:
                raise RuntimeError(f"Refusing to overwrite immutable release file: {key}")

    mutable = sorted(key for key in planned if not key.startswith(release_prefix) and key != pointer_key)
    snapshot = {key: _remote_bytes(client, bucket, key) for key in [*mutable, pointer_key]}
    rollback_dir = ROOT / "backups" / "rollback"
    rollback_dir.mkdir(parents=True, exist_ok=True)
    rollback_path = rollback_dir / f"{release_id}.json"
    rollback = {"release_id": old_release, "files": {
        key: base64.b64encode(value).decode("ascii") if value is not None else None
        for key, value in snapshot.items()
    }}
    encoded = json.dumps(rollback, indent=2, sort_keys=True) + "\n"
    if rollback_path.exists():
        if rollback_path.read_text(encoding="utf-8") != encoded:
            raise RuntimeError("Rollback snapshot already exists for different live metadata; use a new release ID")
    else:
        rollback_path.write_text(encoded, encoding="utf-8")

    if bool(publishing.get("r2_manage_cors", False)):
        client.put_bucket_cors(Bucket=bucket, CORSConfiguration={"CORSRules": [{
            "AllowedMethods": ["GET", "HEAD"],
            "AllowedOrigins": ["https://www.jtorrent.net", "https://jtorrent.net"],
            "AllowedHeaders": ["*"], "ExposeHeaders": ["ETag"], "MaxAgeSeconds": 86400,
        }]})

    uploaded = 0
    changed_metadata: list[str] = []

    def upload(key: str) -> None:
        nonlocal uploaded
        path = public_dir / key
        client.upload_file(str(path), bucket, key, ExtraArgs={
            **object_args(path, key), "Metadata": {"sha256": digests[key]},
        })
        uploaded += 1
        head = client.head_object(Bucket=bucket, Key=key)
        if head.get("ContentLength") != planned[key] or head.get("Metadata", {}).get("sha256") != digests[key]:
            raise RuntimeError(f"Uploaded object failed verification: {key}")

    for key in immutable:
        if key not in existing_sizes:
            upload(key)
    if _remote_bytes(client, bucket, pointer_key) != old_pointer:
        raise RuntimeError("Live pointer changed during upload; leaving it unchanged")
    try:
        for key in mutable:
            changed_metadata.append(key)
            upload(key)
        if _remote_bytes(client, bucket, pointer_key) != old_pointer:
            raise RuntimeError("Live pointer changed before activation")
        upload(pointer_key)
        if _remote_bytes(client, bucket, pointer_key) != (public_dir / pointer_key).read_bytes():
            raise RuntimeError("Live pointer failed read-back verification")
    except Exception:
        # A failed/ambiguous pointer write may have succeeded. Never prune or
        # replace metadata belonging to an activated/concurrently changed release.
        if _remote_bytes(client, bucket, pointer_key) == old_pointer:
            for key in changed_metadata:
                if snapshot[key] is not None:
                    client.put_object(Bucket=bucket, Key=key, Body=snapshot[key], **object_args(public_dir / key, key))
                else:
                    delete_keys(client, bucket, [key])
        raise

    by_release: dict[str, list[str]] = defaultdict(list)
    for row in list_objects(client, bucket):
        parts = str(row["Key"]).split("/")
        if len(parts) > 4 and parts[:3] == ["data", "v4", "releases"]:
            by_release[parts[3]].append(str(row["Key"]))
    protected = {release_id, old_release}
    stale = sorted(value for value in by_release if value not in protected)
    while len(by_release) > keep_versions and stale:
        delete_keys(client, bucket, by_release.pop(stale.pop(0)))
    result = {"release_id": release_id, "bucket": bucket, "uploaded_files": uploaded,
              "projected_bytes": projected, "previous_release_id": old_release,
              "rollback_snapshot": str(rollback_path)}
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
