from jtorrent_backend.models import TorrentItem
from jtorrent_backend.normalize import extract_infohash_from_magnet, format_size, normalize_item, parse_size_to_bytes, slugify


def test_slugify():
    assert slugify("Ubuntu 24.04 Desktop ISO") == "ubuntu-24-04-desktop-iso"


def test_size_parse_and_format():
    assert parse_size_to_bytes("1.5 GiB") == 1610612736
    assert format_size(1610612736) == "1.5 GiB"


def test_magnet_infohash():
    ih = "0123456789abcdef0123456789abcdef01234567"
    assert extract_infohash_from_magnet(f"magnet:?xt=urn:btih:{ih}&dn=test") == ih


def test_normalize_item_assigns_ids():
    item = normalize_item(TorrentItem(title="Test Torrent", source_id="manual", size="1 KiB"))
    assert item.id.startswith("manual-")
    assert item.slug == "test-torrent"
    assert item.size_bytes == 1024


def test_normalize_item_handles_list_text_fields():
    item = normalize_item(
        TorrentItem(
            title=["A", "List", "Title"],
            source_id="source",
            description=["first", "second"],
            license_url=["https://example.com/license"],
            tags="alpha,beta",
        )
    )

    assert item.title == "A List Title"
    assert item.normalized_title == "a list title"
    assert item.description == "first second"
    assert item.license_url == "https://example.com/license"
    assert item.tags == ["alpha", "beta"]
