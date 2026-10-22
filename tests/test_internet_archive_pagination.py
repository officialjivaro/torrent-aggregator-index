from __future__ import annotations

from urllib.parse import parse_qs, urlparse

from jtorrent_backend.sources.internet_archive import InternetArchiveAdvancedSearchSource


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def json(self):
        return self.payload


class FakeHttp:
    def __init__(self):
        self.urls: list[str] = []

    def get(self, url: str):
        self.urls.append(url)
        query = parse_qs(urlparse(url).query)
        page = int(query["page"][0])
        rows = int(query["rows"][0])
        docs = [
            {
                "identifier": f"item-{page}-{i}",
                "title": f"Item {page}-{i}",
                "publicdate": "2026-06-01T00:00:00Z",
                "downloads": "123",
                "item_size": "1024",
            }
            for i in range(rows)
        ]
        return FakeResponse({"response": {"docs": docs}})


def test_internet_archive_advancedsearch_paginates_until_max_items() -> None:
    source = {
        "id": "ia_test",
        "name": "IA Test",
        "type": "internet_archive_advancedsearch",
        "category": "test",
        "query": "collection:test",
        "rows": 2,
        "max_pages": 10,
        "max_items": 5,
        "include_torrent_url": True,
    }
    http = FakeHttp()
    adapter = InternetArchiveAdvancedSearchSource(source, http)  # type: ignore[arg-type]

    items = list(adapter.fetch())

    assert len(items) == 5
    assert len(http.urls) == 3
    assert items[0].torrent_url == "https://archive.org/download/item-1-0/item-1-0_archive.torrent"
    assert items[-1].title == "Item 3-0"


def test_internet_archive_advancedsearch_handles_list_valued_fields() -> None:
    class ListFieldHttp:
        def get(self, url: str):
            return FakeResponse(
                {
                    "response": {
                        "docs": [
                            {
                                "identifier": ["list-id"],
                                "title": ["List", "Title"],
                                "description": ["First sentence.", "Second sentence."],
                                "licenseurl": ["https://creativecommons.org/licenses/by/4.0/"],
                                "publicdate": ["2026-06-01T00:00:00Z"],
                                "date": ["2025-01-01"],
                                "downloads": ["123"],
                                "item_size": ["2048"],
                            }
                        ]
                    }
                }
            )

    source = {
        "id": "ia_test",
        "name": "IA Test",
        "type": "internet_archive_advancedsearch",
        "category": "test",
        "query": "collection:test",
        "rows": 1,
        "max_pages": 1,
        "max_items": 1,
        "include_torrent_url": True,
    }
    adapter = InternetArchiveAdvancedSearchSource(source, ListFieldHttp())  # type: ignore[arg-type]

    item = list(adapter.fetch())[0]

    assert item.title == "List Title"
    assert item.description == "First sentence. Second sentence."
    assert item.license_url == "https://creativecommons.org/licenses/by/4.0/"
    assert item.date_added == "2026-06-01"
    assert item.date_published == "2025-01-01"
    assert item.size_bytes == 2048
    assert item.completed == 123


class FakeScrapeHttp:
    def __init__(self, pages: int, per_page: int):
        self.urls: list[str] = []
        self.pages = pages
        self.per_page = per_page

    def get(self, url: str):
        self.urls.append(url)
        query = parse_qs(urlparse(url).query)
        page = int(query.get("cursor", ["0"])[0])
        docs = [{"identifier": f"scrape-{page}-{i}", "title": f"Scrape {page}-{i}", "downloads": 9} for i in range(self.per_page)]
        payload = {"items": docs, "count": len(docs)}
        if page + 1 < self.pages:
            payload["cursor"] = str(page + 1)
        return FakeResponse(payload)


def test_internet_archive_scrape_follows_cursor_past_deep_paging_limit() -> None:
    source = {
        "id": "ia_scrape",
        "name": "IA Scrape",
        "type": "internet_archive_advancedsearch",
        "api": "scrape",
        "category": "test",
        "query": "collection:test",
        "sorts": "downloads desc",
        "rows": 100,
        "max_items": 250,
        "include_torrent_url": True,
    }
    http = FakeScrapeHttp(pages=5, per_page=100)
    items = list(InternetArchiveAdvancedSearchSource(source, http).fetch())  # type: ignore[arg-type]

    assert len(items) == 250
    assert len(http.urls) == 3
    first = parse_qs(urlparse(http.urls[0]).query)
    assert "cursor" not in first and first["sorts"] == ["downloads desc"] and first["count"] == ["100"]
    assert parse_qs(urlparse(http.urls[1]).query)["cursor"] == ["1"]
    assert items[0].torrent_url == "https://archive.org/download/scrape-0-0/scrape-0-0_archive.torrent"


def test_internet_archive_scrape_stops_when_cursor_ends() -> None:
    source = {"id": "ia_scrape", "name": "IA Scrape", "type": "internet_archive_advancedsearch", "api": "scrape", "category": "test", "query": "q", "rows": 100, "max_items": 0}
    http = FakeScrapeHttp(pages=2, per_page=100)
    items = list(InternetArchiveAdvancedSearchSource(source, http).fetch())  # type: ignore[arg-type]
    assert len(items) == 200 and len(http.urls) == 2
