#!/usr/bin/env python3
"""Refuse to publish a generated index that is unhealthy or much smaller than the live release."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Callable

import requests

DEFAULT_LIVE_STATUS_URL = "https://index.jtorrent.net/data/status.json"
BAD_REQUIRED_STATES = {"failed", "empty"}


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def fetch_live_status(url: str) -> dict[str, Any]:
    response = requests.get(url, timeout=30, headers={"Cache-Control": "no-cache"})
    response.raise_for_status()
    return response.json()


def evaluate(
    candidate_status: dict[str, Any],
    candidate_health: dict[str, Any],
    live_status: dict[str, Any] | None,
    *,
    max_item_drop: float,
    max_scope_drop: float,
    max_healthy_drop: int,
) -> list[str]:
    """Return the reasons the candidate must not be published (empty when it passes)."""
    errors: list[str] = []
    items = int(candidate_status.get("item_count", 0))
    healthy = int(candidate_status.get("healthy_source_count", 0))

    for source in candidate_health.get("sources", []):
        if source.get("required") and source.get("state") in BAD_REQUIRED_STATES:
            errors.append(f"Required source {source.get('id')} is {source.get('state')}.")

    if live_status is None:
        return errors

    live_items = int(live_status.get("item_count", 0))
    if live_items and items < live_items * (1 - max_item_drop):
        errors.append(f"Item count {items:,} is more than {max_item_drop:.0%} below live {live_items:,}.")

    live_scopes = live_status.get("counts_by_scope") or {}
    candidate_scopes = candidate_status.get("counts_by_scope") or {}
    for scope, live_count in live_scopes.items():
        count = int(candidate_scopes.get(scope, 0))
        if int(live_count) and count < int(live_count) * (1 - max_scope_drop):
            errors.append(f"Scope {scope} has {count:,} records, more than {max_scope_drop:.0%} below live {int(live_count):,}.")

    live_healthy = int(live_status.get("healthy_source_count", 0))
    if healthy < live_healthy - max_healthy_drop:
        errors.append(f"Healthy sources fell from {live_healthy} to {healthy}.")
    return errors


def main(argv: list[str] | None = None, fetch: Callable[[str], dict[str, Any]] = fetch_live_status) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--public-dir", default="public")
    parser.add_argument("--live-status-url", default=DEFAULT_LIVE_STATUS_URL)
    parser.add_argument("--max-item-drop", type=float, default=0.10)
    parser.add_argument("--max-scope-drop", type=float, default=0.15)
    parser.add_argument("--max-healthy-drop", type=int, default=2)
    parser.add_argument("--allow-missing-live", action="store_true", help="Pass when the live status cannot be read (first publish only)")
    parser.add_argument("--report", default=None)
    args = parser.parse_args(argv)

    public = Path(args.public_dir)
    candidate_status = read_json(public / "data/status.json")
    candidate_health = read_json(public / "data/source-health.json")

    live_status: dict[str, Any] | None = None
    errors: list[str] = []
    try:
        live_status = fetch(args.live_status_url)
    except Exception as exc:  # noqa: BLE001 - any failure to read live state fails closed
        if not args.allow_missing_live:
            errors.append(f"Could not read live status ({exc}); refusing to publish without a comparison.")

    errors += evaluate(
        candidate_status,
        candidate_health,
        live_status,
        max_item_drop=args.max_item_drop,
        max_scope_drop=args.max_scope_drop,
        max_healthy_drop=args.max_healthy_drop,
    )

    report = {
        "passed": not errors,
        "errors": errors,
        "candidate": {k: candidate_status.get(k) for k in ("release_id", "item_count", "healthy_source_count", "counts_by_scope")},
        "live": None if live_status is None else {k: live_status.get(k) for k in ("release_id", "item_count", "healthy_source_count", "counts_by_scope")},
    }
    if args.report:
        Path(args.report).parent.mkdir(parents=True, exist_ok=True)
        Path(args.report).write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")

    if errors:
        print("Publish gate FAILED; nothing will be published:")
        for error in errors:
            print(f"- {error}")
        return 1
    live_items = live_status.get("item_count") if live_status else "n/a"
    print(f"Publish gate passed: {candidate_status.get('item_count'):,} items (live {live_items}), {candidate_status.get('healthy_source_count')} healthy sources.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
