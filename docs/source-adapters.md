# Source adapters

The source list is the allowlist. Sources with `enabled: true` are fetched; sources with `enabled: false` are skipped. Fields like `license`, `license_url`, and `copyright_status` are output metadata.

## `html_torrent_links`

Use this for pages that list `.torrent` or magnet links.

```yaml
- id: example_official
  name: Example Official Downloads
  type: html_torrent_links
  enabled: true
  category: linux
  source_homepage: https://example.org/downloads
  page_urls:
    - https://example.org/downloads/torrents/
  include_url_regexes:
    - "\\.torrent(?:$|[?#])"
  collect_magnets: true
```

For paginated HTML:

```yaml
page_url_templates:
  - template: "https://example.org/browse?page={page}"
    start: 1
    count: 50
```

The adapter checks `href`, `data-href`, `data-url`, `data-link`, `data-download`, `data-magnet`, and `value`, then scans raw HTML for magnet and `.torrent` URLs.

## `rss_feed`

Use this for feeds that include torrent or magnet links.

```yaml
- id: example_rss
  name: Example RSS
  type: rss_feed
  enabled: true
  category: datasets
  source_homepage: https://example.org
  feed_urls:
    - https://example.org/feed.xml
```

For paginated feeds:

```yaml
feed_url_templates:
  - template: "https://example.org/feed?page={page}"
    start: 1
    count: 20
```

## `internet_archive_advancedsearch`

Use this for Internet Archive advanced-search queries.

```yaml
- id: ia_example
  name: Internet Archive Example
  type: internet_archive_advancedsearch
  enabled: true
  category: public-domain
  source_homepage: https://archive.org
  query: "collection:prelinger AND mediatype:movies"
  rows: 1000
  max_pages: 250
  max_items: 250000
  include_torrent_url: true
```

## `direct_list`

Use this for fixed records.

```yaml
- id: manual_project
  name: Manual Project Torrents
  type: direct_list
  enabled: true
  category: open-source
  items:
    - title: Project ISO
      source_url: https://project.example/download
      torrent_url: https://project.example/project.iso.torrent
```

## Large-source controls

All adapters respect the builder-level cap. A source-level `max_items` overrides `settings.limits.max_items_per_source`. `internet_archive_advancedsearch` also supports `rows` and `max_pages`.

Set `required: false` for best-effort sources whose domains or markup are volatile. Their errors stay visible in `sources.json` and manifest warnings, while required-source errors still block QC.
