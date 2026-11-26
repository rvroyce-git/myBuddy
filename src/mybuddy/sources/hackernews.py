"""Hacker News top stories filtered by keywords (official Firebase API)."""

from __future__ import annotations

import re
from datetime import timedelta
from typing import Any

from mybuddy.models import Clock, HttpClient, Item
from mybuddy.sources._common import (
    clean_snippet,
    clean_title,
    from_epoch,
    is_web_url,
    newest_first,
    spec_int,
    spec_number,
    spec_str,
    spec_str_list,
)

HN_API = "https://hacker-news.firebaseio.com/v0"
HN_ITEM_PAGE = "https://news.ycombinator.com/item?id="


class HackerNewsSource:
    """Top stories whose title contains any keyword (case-insensitive, whole word)."""

    def __init__(
        self,
        name: str,
        keywords: list[str],
        http: HttpClient,
        clock: Clock,
        *,
        min_score: int = 50,
        limit: int = 10,
        max_age_hours: float = 48,
        scan: int = 60,
    ) -> None:
        self.name = name
        self.keywords = keywords
        self.min_score = min_score
        self.limit = limit
        self.max_age_hours = max_age_hours
        self.scan = scan
        self._http = http
        self._clock = clock
        alternatives = "|".join(re.escape(k) for k in keywords)
        self._pattern = re.compile(rf"(?<!\w)(?:{alternatives})(?!\w)", re.IGNORECASE)

    def collect(self) -> list[Item]:
        ids = self._http.get_json(f"{HN_API}/topstories.json")
        if not isinstance(ids, list):
            raise ValueError(f"{self.name}: unexpected topstories response")
        cutoff = self._clock.now() - timedelta(hours=self.max_age_hours)
        items: list[Item] = []
        for story_id in ids[: self.scan]:
            if isinstance(story_id, bool) or not isinstance(story_id, int):
                continue
            try:
                story = self._http.get_json(f"{HN_API}/item/{story_id}.json")
            except Exception:  # one missing story must not sink the whole source
                continue
            item = self._to_item(story, story_id)
            if item is not None and (item.published is None or item.published >= cutoff):
                items.append(item)
        return newest_first(items)[: self.limit]

    def _to_item(self, story: Any, story_id: int) -> Item | None:
        if not isinstance(story, dict) or story.get("type") != "story":
            return None
        if story.get("dead") or story.get("deleted"):
            return None
        title = clean_title(str(story.get("title") or ""))
        score = story.get("score")
        if not title or not isinstance(score, int) or score < self.min_score:
            return None
        if not self._pattern.search(title):
            return None
        discussion = f"{HN_ITEM_PAGE}{story_id}"
        url = story.get("url")
        if not is_web_url(url):
            url = discussion
        comments = story.get("descendants")
        return Item(
            title=title,
            url=str(url),
            source=self.name,
            published=from_epoch(story.get("time")),
            snippet=clean_snippet(str(story.get("text") or "")),
            extra={"score": score, "comments": comments, "discussion_url": discussion},
        )


def build_hackernews(spec: dict[str, Any], http: HttpClient, clock: Clock) -> HackerNewsSource:
    return HackerNewsSource(
        spec_str(spec, "name"),
        spec_str_list(spec, "keywords"),
        http,
        clock,
        min_score=spec_int(spec, "min_score", 50, minimum=0),
        limit=spec_int(spec, "limit", 10),
        max_age_hours=spec_number(spec, "max_age_hours", 48, minimum=1),
        scan=spec_int(spec, "scan", 60),
    )
