from __future__ import annotations

from jtorrent_backend.sources.html_torrent_links import HtmlTorrentLinksSource
from jtorrent_backend.sources.rss_feed import RssFeedSource


class HtmlTemplateHttp:
    def __init__(self):
        self.urls: list[str] = []

    def get_text(self, url: str) -> str:
        self.urls.append(url)
        page = url.rsplit("=", 1)[-1]
        return f'<a href="/item-{page}.torrent">Item {page}</a>'


def test_html_source_expands_page_url_templates() -> None:
    source = {
        "id": "html_pages",
        "name": "HTML Pages",
        "type": "html_torrent_links",
        "category": "test",
        "page_url_templates": [{"template": "https://example.com/browse?page={page}", "start": 1, "count": 3}],
        "include_url_regexes": [r"\.torrent(?:$|[?#])"],
        "max_items": 10,
    }
    http = HtmlTemplateHttp()
    adapter = HtmlTorrentLinksSource(source, http)  # type: ignore[arg-type]

    items = list(adapter.fetch())

    assert len(items) == 3
    assert http.urls == [
        "https://example.com/browse?page=1",
        "https://example.com/browse?page=2",
        "https://example.com/browse?page=3",
    ]


class RssTemplateHttp:
    def __init__(self):
        self.urls: list[str] = []

    def get_text(self, url: str) -> str:
        self.urls.append(url)
        page = url.rsplit("=", 1)[-1]
        return f"""<?xml version='1.0'?><rss><channel><item><title>Item {page}</title><link>https://example.com/{page}</link><enclosure url='https://example.com/{page}.torrent' type='application/x-bittorrent'/></item></channel></rss>"""


def test_rss_source_expands_feed_url_templates() -> None:
    source = {
        "id": "rss_pages",
        "name": "RSS Pages",
        "type": "rss_feed",
        "category": "test",
        "feed_url_templates": [{"template": "https://example.com/feed?page={page}", "start": 1, "count": 2}],
    }
    http = RssTemplateHttp()
    adapter = RssFeedSource(source, http)  # type: ignore[arg-type]

    items = list(adapter.fetch())

    assert len(items) == 2
    assert items[0].torrent_url == "https://example.com/1.torrent"
    assert http.urls == ["https://example.com/feed?page=1", "https://example.com/feed?page=2"]
