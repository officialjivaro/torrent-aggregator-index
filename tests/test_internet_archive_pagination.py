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
