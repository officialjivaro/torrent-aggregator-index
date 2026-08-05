from jtorrent_backend.dedupe import dedupe_items
from jtorrent_backend.models import TorrentItem


def test_dedupe_by_infohash_merges_tags():
    a = TorrentItem(title="A", source_id="one", infohash="abc", tags=["linux"], torrent_url="https://a.example/a.torrent")
    b = TorrentItem(title="A mirror", source_id="two", infohash="abc", tags=["iso"], torrent_url="https://b.example/b.torrent")
    out = dedupe_items([a, b])
    assert len(out) == 1
    assert "linux" in out[0].tags
    assert "iso" in out[0].tags
    assert len(out[0].mirrors) >= 2
