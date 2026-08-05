from __future__ import annotations

from jtorrent_backend.sources.html_torrent_links import HtmlTorrentLinksSource


class FakeHttp:
    def get_text(self, url: str) -> str:
        return """
        <html><body>
          <a data-href="/downloads/example.iso.torrent">Data attr torrent 1.5 GiB 2026-06-03</a>
          <script>const magnet = "magnet:?xt=urn:btih:0123456789abcdef0123456789abcdef01234567&dn=Example";</script>
        </body></html>
        """


def test_html_source_reads_data_attributes_and_raw_magnets() -> None:
    source = {
        "id": "html_test",
        "name": "HTML Test",
        "type": "html_torrent_links",
        "category": "test",
        "page_urls": ["https://example.com/page"],
        "include_url_regexes": [r"\.torrent(?:$|[?#])", r"^magnet:"],
        "collect_magnets": True,
    }
    adapter = HtmlTorrentLinksSource(source, FakeHttp())  # type: ignore[arg-type]

    items = list(adapter.fetch())

    assert len(items) == 2
    assert items[0].torrent_url == "https://example.com/downloads/example.iso.torrent"
    assert items[0].size_bytes == 1610612736
    assert items[0].date_added == "2026-06-03"
    assert items[1].magnet and items[1].magnet.startswith("magnet:?")
