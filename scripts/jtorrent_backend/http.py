from __future__ import annotations

import time
from dataclasses import dataclass

import requests


@dataclass
class HttpSettings:
    user_agent: str = "JTorrentBackend/0.1"
    timeout_seconds: int = 25
    delay_seconds: float = 1.0
    retries: int = 2
    max_torrent_bytes: int = 5 * 1024 * 1024


class HttpClient:
    def __init__(self, settings: HttpSettings):
        self.settings = settings
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": settings.user_agent,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,application/rss+xml;q=0.8,application/json;q=0.8,*/*;q=0.7",
            "Accept-Language": "en-US,en;q=0.9",
        })
        self._last_request = 0.0

    def _throttle(self) -> None:
        elapsed = time.time() - self._last_request
        wait = self.settings.delay_seconds - elapsed
        if wait > 0:
            time.sleep(wait)

    def get(self, url: str, *, stream: bool = False) -> requests.Response:
        last_exc: Exception | None = None
        for attempt in range(self.settings.retries + 1):
            try:
                self._throttle()
                response = self.session.get(url, timeout=self.settings.timeout_seconds, stream=stream)
                self._last_request = time.time()
                response.raise_for_status()
                return response
            except Exception as exc:  # noqa: BLE001
                last_exc = exc
                if attempt < self.settings.retries:
                    time.sleep(1.5 * (attempt + 1))
        raise RuntimeError(f"GET failed for {url}: {last_exc}")

    def get_text(self, url: str) -> str:
        response = self.get(url)
        if not response.encoding:
            response.encoding = response.apparent_encoding
        return response.text

    def get_limited_bytes(self, url: str, max_bytes: int | None = None) -> bytes:
        limit = max_bytes or self.settings.max_torrent_bytes
        response = self.get(url, stream=True)
        chunks: list[bytes] = []
        total = 0
        for chunk in response.iter_content(chunk_size=65536):
            if not chunk:
                continue
            total += len(chunk)
            if total > limit:
                raise ValueError(f"Downloaded file exceeds max bytes limit: {url}")
            chunks.append(chunk)
        return b"".join(chunks)


def make_http_settings(settings: dict) -> HttpSettings:
    request = settings.get("request", {}) or {}
    return HttpSettings(
        user_agent=request.get("user_agent", "JTorrentBackend/0.1"),
        timeout_seconds=int(request.get("timeout_seconds", 25)),
        delay_seconds=float(request.get("delay_seconds", 1.0)),
        retries=int(request.get("retries", 2)),
        max_torrent_bytes=int(request.get("max_torrent_bytes", 5 * 1024 * 1024)),
    )
