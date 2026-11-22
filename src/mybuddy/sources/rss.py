"""Generic RSS/Atom feeds (and Google News search, which is an RSS feed)."""

from __future__ import annotations

from dataclasses import replace
from datetime import timedelta
from typing import Any
from urllib.parse import urlencode

import feedparser

from mybuddy.models import Clock, HttpClient, Item
from mybuddy.sources._common import (
    clean_snippet,
    clean_title,
    from_struct_time,
    is_web_url,
    newest_first,
    spec_int,
    spec_number,
    spec_str,
)

FEED_ACCEPT = "application/rss+xml, application/atom+xml, application/xml;q=0.9, text/xml;q=0.8"


def parse_feed(data: bytes, source_name: str) -> list[Item]:
    """Parse RSS/Atom bytes into Items (unsorted, unfiltered except for bad entries)."""
    parsed: Any = feedparser.parse(data)
    entries = list(parsed.get("entries") or [])
    if not entries and parsed.get("bozo"):
        raise ValueError(f"{source_name}: could not parse feed: {parsed.get('bozo_exception')}")
    items: list[Item] = []
    for entry in entries:
        title = clean_title(str(entry.get("title") or ""))
        url = str(entry.get("link") or "").strip()
        if not title or not is_web_url(url):
            continue
        published = from_struct_time(entry.get("published_parsed")) or from_struct_time(
            entry.get("updated_parsed")
        )
        summary = str(entry.get("summary") or "")
        items.append(
            Item(
                title=title,
                url=url,
                source=source_name,
                published=published,
                snippet=clean_snippet(summary),
            )
        )
    return items


class RssSource:
    """Any RSS or Atom feed. Keeps entries newer than ``max_age_hours`` (undated kept)."""

    def __init__(
        self,
        name: str,
        url: str,
        http: HttpClient,
        clock: Clock,
        *,
        max_age_hours: float = 48,
        limit: int = 10,
    ) -> None:
        if not is_web_url(url):
            raise ValueError(f"source {name!r}: url must be an http(s) URL")
        self.name = name
        self.url = url
        self._http = http
        self._clock = clock
        self.max_age_hours = max_age_hours
        self.limit = limit

    def collect(self) -> list[Item]:
        data = self._http.get_bytes(self.url, accept=FEED_ACCEPT)
        cutoff = self._clock.now() - timedelta(hours=self.max_age_hours)
        items = [
            it
            for it in parse_feed(data, self.name)
            if it.published is None or it.published >= cutoff
        ]
        return newest_first(items)[: self.limit]


class GoogleNewsSource(RssSource):
    """Google News search. Its titles end with " - Publisher"; show that publisher as the
    source and keep the title clean."""

    def collect(self) -> list[Item]:
        return [split_publisher(item) for item in super().collect()]


def split_publisher(item: Item) -> Item:
    head, sep, publisher = item.title.rpartition(" - ")
    publisher = publisher.strip()
    if not sep or not head.strip() or not publisher or len(publisher) > 60:
        return item
    return replace(item, title=head.strip(), source=publisher)


def google_news_url(query: str) -> str:
    params = {"q": query, "hl": "en-IN", "gl": "IN", "ceid": "IN:en"}
    return "https://news.google.com/rss/search?" + urlencode(params)


def build_rss(spec: dict[str, Any], http: HttpClient, clock: Clock) -> RssSource:
    return RssSource(
        spec_str(spec, "name"),
        spec_str(spec, "url"),
        http,
        clock,
        max_age_hours=spec_number(spec, "max_age_hours", 48, minimum=1),
        limit=spec_int(spec, "limit", 10),
    )


def build_google_news(spec: dict[str, Any], http: HttpClient, clock: Clock) -> RssSource:
    return GoogleNewsSource(
        spec_str(spec, "name"),
        google_news_url(spec_str(spec, "query")),
        http,
        clock,
        max_age_hours=spec_number(spec, "max_age_hours", 48, minimum=1),
        limit=spec_int(spec, "limit", 10),
    )
