from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass

import requests


@dataclass
class HttpSettings:
    user_agent: str = "JTorrentBackend/0.1"
    timeout_seconds: int = 25
    delay_seconds: float = 1.0
    retries: int = 2
    max_torrent_bytes: int = 5 * 1024 * 1024


class HttpRequestError(RuntimeError):
    def __init__(
        self,
        url: str,
        cause: Exception,
        *,
        status_code: int | None = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        super().__init__(f"GET failed for {url}: {cause}")
        self.url = url
        self.cause = cause
        self.status_code = status_code
        self.headers = {str(key).lower(): str(value) for key, value in (headers or {}).items()}


class HttpClient:
    def __init__(
        self,
        settings: HttpSettings,
        *,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
    ):
        self.settings = settings
        self._sleep = sleep
        self._clock = clock
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": settings.user_agent,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,application/rss+xml;q=0.8,application/json;q=0.8,*/*;q=0.7",
            "Accept-Language": "en-US,en;q=0.9",
        })
        self._last_request = 0.0

    def sleep(self, seconds: float) -> None:
        if seconds > 0:
            self._sleep(seconds)

    def _throttle(self, delay_seconds: float | None = None) -> None:
        delay = self.settings.delay_seconds if delay_seconds is None else max(0.0, float(delay_seconds))
        elapsed = self._clock() - self._last_request
        wait = delay - elapsed
        if wait > 0:
            self.sleep(wait)

    def get(
        self,
        url: str,
        *,
        stream: bool = False,
        retries: int | None = None,
        delay_seconds: float | None = None,
    ) -> requests.Response:
        last_exc: HttpRequestError | None = None
        retry_count = self.settings.retries if retries is None else max(0, int(retries))
        for attempt in range(retry_count + 1):
            try:
                self._throttle(delay_seconds)
                response = self.session.get(url, timeout=self.settings.timeout_seconds, stream=stream)
                self._last_request = self._clock()
                response.raise_for_status()
                return response
            except Exception as exc:  # noqa: BLE001
                response = getattr(exc, "response", None)
                last_exc = HttpRequestError(
                    url,
                    exc,
                    status_code=int(response.status_code) if response is not None else None,
                    headers=dict(response.headers) if response is not None else None,
                )
                if attempt < retry_count:
                    self.sleep(1.5 * (attempt + 1))
        if last_exc is None:  # pragma: no cover - the loop always executes
            last_exc = HttpRequestError(url, RuntimeError("request did not run"))
        raise last_exc

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
