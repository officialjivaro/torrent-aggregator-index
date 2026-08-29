from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
import requests

from jtorrent_backend.catalog import CatalogStore
from jtorrent_backend.http import HttpClient, HttpRequestError, HttpSettings
from jtorrent_backend.search_v3 import _source_health
from jtorrent_backend.sources.apibay import ApiBaySource


def row(identifier: int, uploader: str) -> dict[str, str]:
    return {
        "id": str(identifier),
        "name": f"Torrent {identifier}",
        "info_hash": f"{identifier:040x}",
        "category": "600",
        "added": "1700000000",
        "size": "1024",
        "seeders": "1",
        "leechers": "0",
        "username": uploader,
        "status": "member",
    }


class FakeResponse:
    def __init__(self, payload: Any):
        self.payload = payload

    def json(self) -> Any:
        return self.payload


class FakeHttp:
    def __init__(self, actions: list[Any]):
        self.actions = list(actions)
        self.calls: list[tuple[str, int | None, float | None]] = []
        self.sleeps: list[float] = []
        self.settings = SimpleNamespace(retries=2)

    def get(self, url: str, *, retries: int | None = None, delay_seconds: float | None = None):
        self.calls.append((url, retries, delay_seconds))
        action = self.actions.pop(0)
        if isinstance(action, Exception):
            raise action
        return FakeResponse(action)

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)


def rate_limited(*, retry_after: str | None = None) -> HttpRequestError:
    headers = {"Retry-After": retry_after} if retry_after is not None else {}
    return HttpRequestError(
        "https://apibay.org/q.php",
        RuntimeError("429 Too Many Requests"),
        status_code=429,
        headers=headers,
    )


def source(tmp_path, **overrides) -> dict[str, Any]:
    value: dict[str, Any] = {
        "id": "the_pirate_bay",
        "name": "The Pirate Bay",
        "type": "apibay",
        "enabled": True,
        "category": "general",
        "source_homepage": "https://thepiratebay.org/",
        "api_base_url": "https://apibay.org/",
        "copyright_status": "unknown",
        "license": "Unverified listings",
        "top_categories": ["recent"],
        "expand_uploaders": True,
        "uploader_refresh_pages": 3,
        "request_delay_seconds": 0,
        "request_retries": 0,
        "rate_limit_backoff_seconds": [1, 2],
        "rate_limit_wait_budget_seconds": 3,
        "_catalog_path": str(tmp_path / "catalog.sqlite"),
    }
    value.update(overrides)
    return value


def test_rate_limited_uploader_is_deferred_without_losing_checkpoint(tmp_path) -> None:
    configured = source(tmp_path)
    with CatalogStore(tmp_path / "catalog.sqlite") as store:
        store.set_checkpoint("the_pirate_bay", "uploader:ABlocked", 7, "2026-01-01T00:00:00Z")
        store.set_checkpoint("the_pirate_bay", "uploader:BGood", -1, "2026-01-01T00:00:00Z")

    http = FakeHttp([
        [row(1, "ABlocked")],
        rate_limited(),
        rate_limited(),
        rate_limited(),
        [],
    ])
    adapter = ApiBaySource(configured, http)

    items = list(adapter.fetch())

    assert [item.title for item in items] == ["Torrent 1"]
    assert http.sleeps == [1, 2]
    assert all(retries == 0 for _url, retries, _delay in http.calls)
    assert any("ABlocked deferred at page 7" in warning for warning in adapter.warnings)
    assert any("fair queue will resume" in warning for warning in adapter.warnings)
    with CatalogStore(tmp_path / "catalog.sqlite") as store:
        assert store.get_checkpoint("the_pirate_bay", "uploader:ABlocked") == 7
        assert store.get_checkpoint("the_pirate_bay", "uploader:BGood") == -1
        assert store.get_checkpoint("the_pirate_bay", "uploader-queue-cursor") == "ABlocked"
        assert store.get_checkpoint("the_pirate_bay", "uploader-defer:ABlocked") > 0


def test_next_run_rotates_to_uploader_after_rate_limited_cursor(tmp_path) -> None:
    configured = source(tmp_path)
    with CatalogStore(tmp_path / "catalog.sqlite") as store:
        store.set_checkpoint("the_pirate_bay", "uploader:ABlocked", 7, "2026-01-01T00:00:00Z")
        store.set_checkpoint("the_pirate_bay", "uploader:BGood", -1, "2026-01-01T00:00:00Z")
        store.set_checkpoint("the_pirate_bay", "uploader-queue-cursor", "ABlocked", "2026-01-01T00:00:00Z")

    http = FakeHttp([[row(1, "ABlocked")], []])
    adapter = ApiBaySource(configured, http)
    list(adapter.fetch())

    assert "user%3ABGood%3A0" in http.calls[1][0]


def test_retry_after_header_overrides_configured_cooldown(tmp_path) -> None:
    http = FakeHttp([
        [row(1, "Uploader")],
        rate_limited(retry_after="4"),
        [],
    ])
    adapter = ApiBaySource(
        source(tmp_path, rate_limit_backoff_seconds=[1], rate_limit_wait_budget_seconds=10),
        http,
    )

    assert len(list(adapter.fetch())) == 1
    assert http.sleeps == [4]
    assert adapter.warnings == []


def test_source_health_exposes_partial_refresh_as_degraded() -> None:
    health = _source_health([
        {
            "id": "the_pirate_bay",
            "name": "The Pirate Bay",
            "required": False,
            "category": "general",
            "indexed_item_count": 99_258,
            "warnings": ["uploader deferred"],
            "error": None,
        }
    ])

    assert health[0]["state"] == "degraded"
    assert health[0]["warnings"] == ["uploader deferred"]


def test_http_client_exposes_status_and_headers_without_rapid_retries() -> None:
    response = SimpleNamespace(
        status_code=429,
        headers={"Retry-After": "30"},
        raise_for_status=None,
    )
    response.raise_for_status = lambda: (_ for _ in ()).throw(
        requests.HTTPError("429", response=response)
    )
    client = HttpClient(HttpSettings(delay_seconds=0), sleep=lambda _seconds: None)
    client.session = SimpleNamespace(get=lambda *_args, **_kwargs: response)

    with pytest.raises(HttpRequestError) as caught:
        client.get("https://apibay.org/q.php", retries=0)

    assert caught.value.status_code == 429
    assert caught.value.headers["retry-after"] == "30"
