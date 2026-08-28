from __future__ import annotations

from .direct_list import DirectListSource
from .html_torrent_links import HtmlTorrentLinksSource
from .internet_archive import InternetArchiveAdvancedSearchSource
from .rss_feed import RssFeedSource
from .apibay import ApiBaySource
from .yts_meta_search import YtsMetaSearchSource

SOURCE_TYPES = {
    "direct_list": DirectListSource,
    "html_torrent_links": HtmlTorrentLinksSource,
    "rss_feed": RssFeedSource,
    "internet_archive_advancedsearch": InternetArchiveAdvancedSearchSource,
    "apibay": ApiBaySource,
    "yts_meta_search": YtsMetaSearchSource,
}
