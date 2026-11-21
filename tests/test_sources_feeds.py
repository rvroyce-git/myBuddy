"""rss, google_news and arxiv sources (all feedparser based)."""

from __future__ import annotations

from datetime import UTC, datetime
from urllib.parse import parse_qs, urlsplit

import pytest
from test_sources_support import FakeHttp, FixedClock, fixture_bytes

from mybuddy.sources import build_source
from mybuddy.sources.arxiv import arxiv_url
from mybuddy.sources.rss import google_news_url

TC_URL = "https://techcrunch.com/category/startups/feed/"

BAD_FEED = b"""<?xml version="1.0"?>
<rss version="2.0"><channel><title>Mixed</title>
<item><title>Good &amp; <b>bold</b></title><link>https://example.com/good</link>
<description>&lt;p&gt;Hello &lt;a href="x"&gt;there&lt;/a&gt;&lt;/p&gt;</description>
<pubDate>Sat, 21 Nov 2026 10:00:00 +0000</pubDate></item>
<item><title></title><link>https://example.com/no-title</link>
<pubDate>Sat, 21 Nov 2026 11:00:00 +0000</pubDate></item>
<item><title>No link</title><pubDate>Sat, 21 Nov 2026 11:00:00 +0000</pubDate></item>
<item><title>Bad scheme</title><link>javascript:alert(1)</link></item>
<item><title>Undated</title><link>https://example.com/undated</link></item>
<item><title>Too old</title><link>https://example.com/old</link>
<pubDate>Mon, 02 Nov 2026 10:00:00 +0000</pubDate></item>
<item><title>Newer, in IST</title><link>https://example.com/ist</link>
<pubDate>Sat, 21 Nov 2026 20:00:00 +0530</pubDate></item>
</channel></rss>"""


def rss(routes: dict[str, object], **extra: object):  # type: ignore[no-untyped-def]
    spec = {"type": "rss", "name": "TechCrunch Startups", "url": TC_URL, **extra}
    return build_source(spec, FakeHttp(routes), FixedClock())


def test_rss_parses_real_fixture() -> None:
    items = rss({TC_URL: fixture_bytes("rss_techcrunch_startups.xml")}).collect()
    assert len(items) == 4
    first = items[0]
    assert first.title.startswith("Experts warn")
    assert first.url.startswith("https://techcrunch.com/2026/11/20/")
    assert first.source == "TechCrunch Startups"
    assert first.published == datetime(2026, 11, 20, 20, 58, 29, tzinfo=UTC)
    assert "Daniel Ek backs AI researcher’s" in items[1].title  # entity decoded
    for it in items:
        assert "<" not in it.snippet and len(it.snippet) <= 300
        assert len(it.title) <= 200
        assert it.published is not None and it.published.tzinfo == UTC
    dates = [it.published for it in items]
    assert dates == sorted(dates, reverse=True)


def test_rss_limit_and_age() -> None:
    routes = {TC_URL: fixture_bytes("rss_techcrunch_startups.xml")}
    assert len(rss(routes, limit=2).collect()) == 2
    # Clock is 2026-11-22 00:00 UTC; entries are 3.0-9.1 hours apart on Nov 20.
    assert len(rss(routes, max_age_hours=30).collect()) == 2
    assert rss(routes, max_age_hours=1).collect() == []


def test_rss_skips_bad_entries_and_sorts() -> None:
    items = rss({TC_URL: BAD_FEED}).collect()
    titles = [it.title for it in items]
    assert titles == ["Newer, in IST", "Good & bold", "Undated"]
    assert items[0].published == datetime(2026, 11, 21, 14, 30, tzinfo=UTC)
    assert items[1].snippet == "Hello there"
    assert items[2].published is None


def test_rss_garbage_raises() -> None:
    with pytest.raises(ValueError):
        rss({TC_URL: b"\x00\x01 not a feed <<<"}).collect()


def test_rss_http_error_propagates() -> None:
    with pytest.raises(OSError):
        rss({TC_URL: OSError("boom")}).collect()


def test_google_news_url_is_encoded() -> None:
    url = google_news_url('"open source" AI & agents')
    parts = urlsplit(url)
    assert parts.scheme == "https" and parts.netloc == "news.google.com"
    assert parts.path == "/rss/search"
    qs = parse_qs(parts.query)
    assert qs == {
        "q": ['"open source" AI & agents'],
        "hl": ["en-IN"],
        "gl": ["IN"],
        "ceid": ["IN:en"],
    }
    assert " " not in url and '"' not in url


def test_google_news_parses_and_orders() -> None:
    routes = {"https://news.google.com/rss/search?*": fixture_bytes("google_news.xml")}
    spec = {"type": "google_news", "name": "Google News", "query": '"open source" AI'}
    http = FakeHttp(routes)
    items = build_source({**spec, "max_age_hours": 24 * 10}, http, FixedClock()).collect()
    assert http.calls == [google_news_url('"open source" AI')]
    assert len(items) == 5
    dates = [it.published for it in items]
    assert dates == sorted(dates, reverse=True)  # fixture is NOT in date order
    assert items[0].title.startswith("Advancing Science Together")
    assert all(it.url.startswith("https://news.google.com/") for it in items)
    # default 48 h window keeps only the Nov 20 article
    default = build_source(spec, FakeHttp(routes), FixedClock()).collect()
    assert [it.title for it in default] == [items[0].title]
    limited = build_source(
        {**spec, "max_age_hours": 240, "limit": 2}, FakeHttp(routes), FixedClock()
    )
    assert len(limited.collect()) == 2


def test_arxiv_url() -> None:
    url = arxiv_url("cat:cs.AI OR cat:cs.CL", 5)
    assert url.startswith("https://export.arxiv.org/api/query?")
    qs = parse_qs(urlsplit(url).query)
    assert qs["search_query"] == ["cat:cs.AI OR cat:cs.CL"]
    assert qs["sortBy"] == ["submittedDate"]
    assert qs["sortOrder"] == ["descending"]
    assert qs["max_results"] == ["5"]


def test_arxiv_parses_atom_fixture() -> None:
    routes = {"https://export.arxiv.org/api/query?*": fixture_bytes("arxiv.xml")}
    spec = {"type": "arxiv", "name": "arXiv cs.AI", "query": "cat:cs.AI"}
    items = build_source(spec, FakeHttp(routes), FixedClock()).collect()
    assert len(items) == 3
    assert items[0].title.startswith("PlayCraft")
    assert "\n" not in items[0].title
    assert items[0].url == "https://arxiv.org/abs/2611.26624v1"
    assert items[0].published == datetime(2026, 11, 19, 18, 59, 34, tzinfo=UTC)
    assert all(0 < len(it.snippet) <= 300 for it in items)
    dates = [it.published for it in items]
    assert dates == sorted(dates, reverse=True)
    one = build_source({**spec, "limit": 1}, FakeHttp(routes), FixedClock()).collect()
    assert len(one) == 1


def test_arxiv_upgrades_http_links() -> None:
    feed = b"""<?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom">
    <entry><title>T</title><id>http://arxiv.org/abs/1</id>
    <link href="http://arxiv.org/abs/1" rel="alternate" type="text/html"/>
    <published>2026-11-19T00:00:00Z</published><summary>S</summary></entry></feed>"""
    routes = {"https://export.arxiv.org/api/query?*": feed}
    spec = {"type": "arxiv", "name": "a", "query": "q"}
    items = build_source(spec, FakeHttp(routes), FixedClock()).collect()
    assert [it.url for it in items] == ["https://arxiv.org/abs/1"]
