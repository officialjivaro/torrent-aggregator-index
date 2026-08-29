from __future__ import annotations

from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
import time
from typing import Any, Iterable
from urllib.parse import quote, urlencode

from ..catalog import CatalogStore
from ..http import HttpClient, HttpRequestError
from ..models import TorrentItem
from ..normalize import clean_tags
from .base import SourceAdapter


CATEGORY_NAMES = {
    "100": "audio",
    "200": "movies",
    "300": "software",
    "400": "games",
    "500": "general",
    "600": "general",
}


class ApiBayRateLimitDeferred(RuntimeError):
    pass


def _number(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


class ApiBaySource(SourceAdapter):
    def __init__(self, source: dict, http: HttpClient) -> None:
        super().__init__(source, http)
        self._rate_limit_waited = 0.0

    def _retry_after_seconds(self, error: HttpRequestError) -> float | None:
        raw = error.headers.get("retry-after")
        if not raw:
            return None
        try:
            return max(0.0, float(raw))
        except ValueError:
            try:
                parsed = parsedate_to_datetime(raw)
                if parsed.tzinfo is None:
                    parsed = parsed.replace(tzinfo=timezone.utc)
                return max(0.0, (parsed - datetime.now(timezone.utc)).total_seconds())
            except (TypeError, ValueError, OverflowError):
                return None

    def _payload(self, url: str, params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        if params:
            url = f"{url}?{urlencode(params)}"
        request_delay = max(0.0, float(self.source.get("request_delay_seconds", 2.0)))
        short_retries = max(0, int(self.source.get("request_retries", self.http.settings.retries)))
        raw_delays = self.source.get("rate_limit_backoff_seconds") or [60, 120, 300, 600]
        rate_delays = [max(0.0, float(value)) for value in raw_delays]
        wait_budget = max(0.0, float(self.source.get("rate_limit_wait_budget_seconds", 1200)))
        short_attempt = 0
        rate_attempt = 0
        while True:
            try:
                response = self.http.get(url, retries=0, delay_seconds=request_delay)
                break
            except HttpRequestError as exc:
                if exc.status_code != 429:
                    if short_attempt >= short_retries:
                        raise
                    short_attempt += 1
                    self.http.sleep(1.5 * short_attempt)
                    continue
                if rate_attempt >= len(rate_delays):
                    raise ApiBayRateLimitDeferred(
                        f"HTTP 429 persisted after {rate_attempt} cooldowns and {self._rate_limit_waited:g}s of run-wide waiting"
                    ) from exc
                wait = self._retry_after_seconds(exc)
                if wait is None:
                    wait = rate_delays[rate_attempt]
                rate_attempt += 1
                remaining = max(0.0, wait_budget - self._rate_limit_waited)
                if wait > remaining:
                    raise ApiBayRateLimitDeferred(
                        f"HTTP 429 requires a {wait:g}s cooldown with only {remaining:g}s left in the run-wide wait budget"
                    ) from exc
                print(f"APIBay rate limited; waiting {wait:g}s before retrying {url}")
                self.http.sleep(wait)
                self._rate_limit_waited += wait

        payload = response.json()
        if not isinstance(payload, list):
            raise RuntimeError(f"APIBay returned a non-list response for {url}")
        return [item for item in payload if isinstance(item, dict) and str(item.get("id") or "0") != "0"]

    def _item(self, row: dict[str, Any]) -> TorrentItem | None:
        infohash = str(row.get("info_hash") or "").strip().lower()
        title = str(row.get("name") or "").strip()
        if not title or not infohash:
            return None
        raw_category = str(row.get("category") or "")
        broad_category = CATEGORY_NAMES.get(raw_category[:1] + "00", self.source.get("category", "general"))
        added = _number(row.get("added"))
        added_iso = datetime.fromtimestamp(added, tz=timezone.utc).isoformat().replace("+00:00", "Z") if added else None
        torrent_id = str(row.get("id") or "").strip()
        username = str(row.get("username") or "").strip()
        tags = [*self.source.get("tags", []), broad_category]
        if username:
            tags.append(username)
        base = self.base_item()
        base.update({
            "title": title,
            "category": broad_category,
            "details_url": f"https://thepiratebay.org/description.php?id={quote(torrent_id)}" if torrent_id else self.source.get("source_homepage"),
            "source_url": f"https://thepiratebay.org/description.php?id={quote(torrent_id)}" if torrent_id else self.source.get("source_homepage"),
            "magnet": f"magnet:?xt=urn:btih:{infohash}&dn={quote(title)}",
            "infohash": infohash,
            "hash_source": "apibay",
            "size_bytes": _number(row.get("size")),
            "seeders": _number(row.get("seeders")),
            "leechers": _number(row.get("leechers")),
            "date_added": added_iso,
            "tags": clean_tags(tags),
            "raw": {
                "apibay_id": torrent_id,
                "uploader": username,
                "category_id": raw_category,
                "uploader_status": row.get("status"),
            },
        })
        return TorrentItem.from_mapping(base)

    def fetch(self) -> Iterable[TorrentItem]:
        api_base = str(self.source.get("api_base_url") or "https://apibay.org/").rstrip("/")
        seen_hashes: set[str] = set()
        uploaders: set[str] = set()
        state_path = self.source.get("_catalog_path")
        store = CatalogStore(Path(state_path)) if state_path else None

        def emit(rows: list[dict[str, Any]]) -> Iterable[TorrentItem]:
            for row in rows:
                item = self._item(row)
                if not item or not item.infohash or item.infohash in seen_hashes:
                    continue
                seen_hashes.add(item.infohash)
                uploader = str(item.raw.get("uploader") or "").strip()
                if uploader and uploader.lower() not in {"anonymous", "anon"}:
                    uploaders.add(uploader)
                yield item

        try:
            for category in self.source.get("top_categories") or ["recent", "all"]:
                url = f"{api_base}/precompiled/data_top100_{category}.json"
                try:
                    yield from emit(self._payload(url))
                except Exception as exc:  # noqa: BLE001
                    self.add_warning(f"top category {category} deferred: {exc}")

            if store:
                for key in store.list_checkpoints(self.source["id"], "uploader:"):
                    uploader = key.removeprefix("uploader:").strip()
                    if uploader:
                        uploaders.add(uploader)

            if self.source.get("expand_uploaders") is not True or not uploaders:
                return

            refresh_pages = max(1, int(self.source.get("uploader_refresh_pages", 3)))
            ordered_uploaders = sorted(uploaders, key=str.lower)
            cursor_key = "uploader-queue-cursor"
            queue_cursor = store.get_checkpoint(self.source["id"], cursor_key) if store else None
            if queue_cursor in ordered_uploaders:
                offset = ordered_uploaders.index(queue_cursor) + 1
                ordered_uploaders = ordered_uploaders[offset:] + ordered_uploaders[:offset]
            defer_seconds = max(60, int(self.source.get("uploader_defer_seconds", 21_600)))
            breaker_threshold = max(1, int(self.source.get("rate_limit_breaker_threshold", 1)))
            persistent_deferrals = 0
            for uploader in ordered_uploaders:
                key = f"uploader:{uploader}"
                defer_key = f"uploader-defer:{uploader}"
                deferred_until = store.get_checkpoint(self.source["id"], defer_key, 0) if store else 0
                try:
                    deferred_until_value = float(deferred_until or 0)
                except (TypeError, ValueError):
                    deferred_until_value = 0
                if deferred_until_value > time.time():
                    if store:
                        store.set_checkpoint(self.source["id"], cursor_key, uploader, self.fetched_at)
                    continue
                checkpoint = store.get_checkpoint(self.source["id"], key) if store else None
                page = 0 if checkpoint in (None, -1) else max(0, int(checkpoint))
                refresh_only = checkpoint == -1
                page_signatures: set[tuple[str, ...]] = set()
                try:
                    while True:
                        rows = self._payload(f"{api_base}/q.php", {"q": f"user:{uploader}:{page}"})
                        signature = tuple(str(row.get("id") or "") for row in rows)
                        if not rows or signature in page_signatures:
                            if store:
                                store.set_checkpoint(self.source["id"], key, -1, self.fetched_at)
                            break
                        page_signatures.add(signature)
                        yield from emit(rows)
                        page += 1
                        if store:
                            store.set_checkpoint(self.source["id"], key, page, self.fetched_at)
                        if len(rows) < 50 or (refresh_only and page >= refresh_pages):
                            if store:
                                store.set_checkpoint(self.source["id"], key, -1, self.fetched_at)
                            break
                except ApiBayRateLimitDeferred as exc:
                    persistent_deferrals += 1
                    if store:
                        store.set_checkpoint(
                            self.source["id"], defer_key, int(time.time()) + defer_seconds, self.fetched_at
                        )
                    self.add_warning(
                        f"uploader {uploader} deferred at page {page} for {defer_seconds}s; "
                        f"checkpoint preserved: {exc}"
                    )
                except Exception as exc:  # noqa: BLE001
                    self.add_warning(f"uploader {uploader} failed at page {page}; checkpoint preserved: {exc}")
                finally:
                    if store:
                        store.set_checkpoint(self.source["id"], cursor_key, uploader, self.fetched_at)
                if persistent_deferrals >= breaker_threshold:
                    self.add_warning(
                        f"uploader expansion stopped after {persistent_deferrals} persistent rate-limit deferral(s); "
                        "the fair queue will resume with the next uploader on the next run"
                    )
                    break
        finally:
            if store:
                store.close()
