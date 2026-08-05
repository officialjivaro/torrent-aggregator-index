from __future__ import annotations

from .direct_list import DirectListSource
from .html_torrent_links import HtmlTorrentLinksSource
from .internet_archive import InternetArchiveAdvancedSearchSource
from .rss_feed import RssFeedSource

SOURCE_TYPES = {
    "direct_list": DirectListSource,
    "html_torrent_links": HtmlTorrentLinksSource,
    "rss_feed": RssFeedSource,
    "internet_archive_advancedsearch": InternetArchiveAdvancedSearchSource,
}
