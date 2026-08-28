from __future__ import annotations

from collections import Counter
from pathlib import Path
from typing import Any

from .catalog import CatalogStore, load_suppressions
from .config import load_config
from .dedupe import dedupe_items
from .http import HttpClient, make_http_settings
from .models import TorrentItem
from .normalize import normalize_item
from .output import DEFAULT_MAX_JSON_FILE_BYTES, write_public
from .sources import SOURCE_TYPES
from .timeutil import utc_now_iso


def _manual_items(config: dict[str, Any], fetched_at: str) -> list[TorrentItem]:
    items: list[TorrentItem] = []
    for raw in config.get("manual_items") or []:
        data = dict(raw)
        data.setdefault("fetched_at", fetched_at)
        items.append(normalize_item(TorrentItem.from_mapping(data)))
    return items


def _as_int(value: Any, default: int) -> int:
    if value is None or value == "":
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _as_bool(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    return str(value).strip().lower() in {"1", "true", "yes", "y", "on"}


def _max_items_for_source(settings: dict[str, Any], source: dict[str, Any]) -> int:
    """Return the effective per-source item cap.

    Source-specific caps intentionally win over the global default. The original
    implementation always used the global max_items_per_source when present,
    which made large source overrides in config/sources.yml ineffective.
    """

    if source.get("max_items") is not None:
        return max(0, _as_int(source.get("max_items"), 750))
    limits = settings.get("limits", {}) or {}
    if limits.get("max_items_per_source") is not None:
        return max(0, _as_int(limits.get("max_items_per_source"), 0))
    return 0


def _record_source_problem(
    *,
    sid: str | None,
    message: str,
    required: bool,
    strict: bool,
    warnings: list[str],
    errors: list[str],
) -> None:
    prefix = sid or "unknown-source"
    formatted = f"{prefix}: {message}"
    if required:
        errors.append(formatted)
    else:
        warnings.append(f"{formatted} [optional source]")
    if strict:
        raise RuntimeError(formatted)


def _add_indexed_counts(source_summaries: list[dict[str, Any]], counts_by_source: Counter[str]) -> None:
    for summary in source_summaries:
        sid = summary.get("id")
        if sid:
            summary["indexed_item_count"] = int(counts_by_source.get(str(sid), 0))


def build_index(config_path: Path, *, offline: bool = False, strict: bool = False) -> dict[str, Any]:
    """Build the static JSON backend from the configured source allowlist.

    The source list itself is the allowlist: if a source has ``enabled: true`` in
    config/sources.yml, the builder attempts to fetch it. If it has
    ``enabled: false`` or is omitted, it is skipped. There is no separate policy,
    blocked-domain list, or copyright-status filter in the builder. Source-level
    fields such as license/copyright_status are treated as optional metadata only.

    A source can also set ``required: false``. Optional source failures are
    recorded as warnings and in sources.json, but they do not block deployment of
    the rest of the generated JSON. Required source failures still populate
    manifest.errors and fail the QC validator.
    """

    config = load_config(config_path)
    settings = config.get("settings", {}) or {}
    limits = settings.get("limits", {}) or {}
    state = settings.get("state", {}) or {}
    catalog_path = Path(state.get("path", "state/catalog.sqlite")) if _as_bool(state.get("enabled"), False) else None
    catalog_soft_limit_bytes = max(0, _as_int(state.get("soft_limit_bytes"), 0))
    suppression_path = Path(settings.get("suppression_path", "config/suppressions.yml"))
    suppressions = load_suppressions(suppression_path)
    generated_at = utc_now_iso()
    warnings: list[str] = []
    errors: list[str] = []

    http = HttpClient(make_http_settings(settings))

    manual_items = _manual_items(config, generated_at)
    all_items: list[TorrentItem] = [] if catalog_path else list(manual_items)
    raw_item_count = len(manual_items)
    catalog = CatalogStore(catalog_path) if catalog_path else None
    if catalog:
        catalog.merge(manual_items, generated_at, catalog_soft_limit_bytes or None)
    source_summaries: list[dict[str, Any]] = []

    enabled_sources = [s for s in config.get("sources", []) if s.get("enabled", False)]

    if not offline:
        for source in enabled_sources:
            sid = source.get("id")
            stype = source.get("type")
            required = _as_bool(source.get("required"), True)
            adapter_cls = SOURCE_TYPES.get(stype)
            max_items = _max_items_for_source(settings, source)
            min_items = max(0, _as_int(source.get("min_items"), 0))
            summary = {
                "id": sid,
                "name": source.get("name"),
                "type": stype,
                "enabled": True,
                "required": required,
                "category": source.get("category"),
                "source_homepage": source.get("source_homepage"),
                "license": source.get("license"),
                "license_url": source.get("license_url"),
                "copyright_status": source.get("copyright_status"),
                "policy_scope": source.get("policy_scope", "open-authorized"),
                "max_items": max_items,
                "min_items": min_items,
                # item_count is the raw number produced by this adapter before
                # cross-source dedupe. indexed_item_count is added after dedupe.
                "item_count": 0,
                "indexed_item_count": 0,
                "reached_max_items": False,
                "error": None,
            }
            if adapter_cls is None:
                msg = f"Unknown source type: {stype}"
                summary["error"] = msg
                source_summaries.append(summary)
                _record_source_problem(
                    sid=sid,
                    message=msg,
                    required=required,
                    strict=strict,
                    warnings=warnings,
                    errors=errors,
                )
                continue

            produced = 0
            catalog_batch: list[TorrentItem] = []
            catalog_batch_size = max(1, _as_int(source.get("catalog_batch_size"), 100))
            try:
                adapter_source = dict(source)
                if catalog_path:
                    adapter_source["_catalog_path"] = str(catalog_path)
                adapter = adapter_cls(adapter_source, http)
                for item in adapter.fetch():
                    if max_items and produced >= max_items:
                        summary["reached_max_items"] = True
                        break
                    item = normalize_item(item)
                    raw_item_count += 1
                    if catalog:
                        catalog_batch.append(item)
                        if len(catalog_batch) >= catalog_batch_size:
                            catalog.merge(catalog_batch, generated_at, catalog_soft_limit_bytes or None)
                            catalog_batch.clear()
                    else:
                        all_items.append(item)
                    produced += 1
                    # Keep the summary accurate even if a later page/doc fails.
                    summary["item_count"] = produced
                if catalog and catalog_batch:
                    catalog.merge(catalog_batch, generated_at, catalog_soft_limit_bytes or None)
                    catalog_batch.clear()
                if max_items and produced >= max_items:
                    summary["reached_max_items"] = True
                    warnings.append(f"{sid}: reached max_items={max_items}")
                if min_items and produced < min_items:
                    msg = f"produced {produced} items, below min_items={min_items}"
                    summary["error"] = msg
                    _record_source_problem(
                        sid=sid,
                        message=msg,
                        required=required,
                        strict=strict,
                        warnings=warnings,
                        errors=errors,
                    )
            except Exception as exc:  # noqa: BLE001
                msg = str(exc)
                summary["item_count"] = produced
                summary["error"] = msg
                _record_source_problem(
                    sid=sid,
                    message=msg,
                    required=required,
                    strict=strict,
                    warnings=warnings,
                    errors=errors,
                )
            source_summaries.append(summary)
    else:
        warnings.append("Offline mode enabled; network sources were skipped.")
        for source in enabled_sources:
            source_summaries.append(
                {
                    "id": source.get("id"),
                    "name": source.get("name"),
                    "type": source.get("type"),
                    "enabled": True,
                    "required": _as_bool(source.get("required"), True),
                    "category": source.get("category"),
                    "source_homepage": source.get("source_homepage"),
                    "license": source.get("license"),
                    "license_url": source.get("license_url"),
                    "copyright_status": source.get("copyright_status"),
                    "policy_scope": source.get("policy_scope", "open-authorized"),
                    "max_items": _max_items_for_source(settings, source),
                    "min_items": max(0, _as_int(source.get("min_items"), 0)),
                    "item_count": 0,
                    "indexed_item_count": 0,
                    "reached_max_items": False,
                    "error": "offline mode",
                }
            )

    active_source_ids = {str(source.get("id")) for source in enabled_sources if source.get("id")}
    active_source_ids.update(str(item.source_id) for item in manual_items if item.source_id)
    suppressed_count = 0
    if catalog:
        items, suppressed_count = catalog.load_active(active_source_ids, suppressions)
        catalog_item_count = catalog.count()
        catalog.record_run(generated_at, raw_item_count, catalog_item_count, suppressed_count)
        catalog.close()
        fetched_item_count = raw_item_count
    else:
        fetched_items = dedupe_items(all_items)
        items = [item for item in fetched_items if not suppressions.matches(item)]
        suppressed_count = len(fetched_items) - len(items)
        catalog_item_count = len(fetched_items)
        fetched_item_count = len(fetched_items)
    items.sort(key=lambda i: ((i.category or ""), (i.title or "").lower()))

    min_total = _as_int(limits.get("min_total_items"), 0)
    if min_total and len(items) < min_total and not offline:
        errors.append(f"total item_count={len(items)} is below min_total_items={min_total}")

    counts_by_category = Counter(i.category or "uncategorized" for i in items)
    counts_by_source = Counter(i.source_id or "unknown" for i in items)
    counts_by_scope = Counter(i.policy_scope or "open-authorized" for i in items)
    _add_indexed_counts(source_summaries, counts_by_source)

    manifest = {
        "name": settings.get("name", "JTorrent Torrent Aggregator Index"),
        "generated_at": generated_at,
        "base_url": settings.get("base_url"),
        "custom_domain": settings.get("custom_domain"),
        "frontend_origin": settings.get("frontend_origin"),
        "item_count": len(items),
        "raw_item_count": raw_item_count,
        "fetched_item_count": fetched_item_count,
        "catalog_item_count": catalog_item_count,
        "suppressed_item_count": suppressed_count,
        "enabled_source_count": len(enabled_sources),
        "required_source_count": sum(1 for s in enabled_sources if _as_bool(s.get("required"), True)),
        "optional_source_count": sum(1 for s in enabled_sources if not _as_bool(s.get("required"), True)),
        "counts_by_category": dict(sorted(counts_by_category.items())),
        "counts_by_source": dict(sorted(counts_by_source.items())),
        "counts_by_scope": dict(sorted(counts_by_scope.items())),
        "policy_scopes": settings.get("policy_scopes", {}),
        "default_index_scope": settings.get("default_index_scope", "open-authorized"),
        "warnings": warnings,
        "errors": errors,
        "source_allowlist_mode": "enabled-sources-only",
    }

    output_dir = Path(settings.get("output_dir", "public"))
    max_file_bytes = _as_int(limits.get("max_json_file_bytes"), DEFAULT_MAX_JSON_FILE_BYTES)
    write_public(output_dir, items, source_summaries, manifest, max_file_bytes=max_file_bytes, output_options=settings.get("output", {}) or {})
    print(f"Built {len(items)} items into {output_dir}")
    if warnings:
        print("Warnings:")
        for warning in warnings:
            print(f"- {warning}")
    if errors:
        print("Errors:")
        for error in errors:
            print(f"- {error}")
    return manifest
