#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from jtorrent_backend.historical import (  # noqa: E402
    HistoricalImportError,
    backup_catalog_once,
    download_archive,
    import_snapshot,
    load_snapshot_registry,
    verify_archive,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="Import a verified historical torrent-index snapshot")
    parser.add_argument("--registry", default="config/historical-snapshots.yml")
    parser.add_argument("--snapshot", default="tpb-archive-2012-10-06")
    parser.add_argument("--catalog", default="state/catalog.sqlite")
    parser.add_argument("--cache-dir", default="historical-cache")
    parser.add_argument("--backup-dir", default="backups")
    parser.add_argument("--archive", default=None, help="Use an already-downloaded archive")
    parser.add_argument("--no-download", action="store_true", help="Fail if the verified archive is not already cached")
    parser.add_argument("--soft-limit-bytes", type=int, default=64 * 1024 * 1024 * 1024)
    parser.add_argument("--batch-size", type=int, default=1000)
    parser.add_argument("--max-chunks", type=int, default=None, help="Import at most this many new chunks")
    args = parser.parse_args()

    try:
        registry = load_snapshot_registry(Path(args.registry))
        if args.snapshot not in registry:
            raise HistoricalImportError(f"Unknown historical snapshot: {args.snapshot}")
        snapshot = registry[args.snapshot]
        if snapshot.get("enabled") is not True:
            raise HistoricalImportError(f"Historical snapshot is disabled: {args.snapshot}")
        cache_dir = Path(args.cache_dir)
        if args.archive:
            archive = Path(args.archive)
            verify_archive(archive, snapshot)
        elif args.no_download:
            archive = cache_dir / str(snapshot["archive_filename"])
            verify_archive(archive, snapshot)
        else:
            archive = download_archive(snapshot, cache_dir)
        backup = backup_catalog_once(Path(args.catalog), Path(args.backup_dir), str(snapshot["id"]))
        if backup:
            print(f"Catalog backup ready: {backup.resolve()}")
        import_snapshot(
            snapshot,
            archive=archive,
            catalog_path=Path(args.catalog),
            cache_dir=cache_dir,
            soft_limit_bytes=max(0, args.soft_limit_bytes),
            batch_size=max(1, args.batch_size),
            max_chunks=max(0, args.max_chunks) if args.max_chunks is not None else None,
            archive_verified=True,
        )
    except Exception as exc:  # noqa: BLE001
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
