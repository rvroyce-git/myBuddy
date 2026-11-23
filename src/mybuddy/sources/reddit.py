"""Top posts of the day from a subreddit (public JSON listing).

Reddit frequently answers 403/429 to unauthenticated clients; errors are simply raised
and the engine isolates the failure.
"""

from __future__ import annotations

import re
from typing import Any

from mybuddy.models import Clock, HttpClient, Item
from mybuddy.sources._common import (
    clean_snippet,
    clean_title,
    from_epoch,
    newest_first,
    spec_int,
    spec_str,
)

_SUBREDDIT = re.compile(r"^[A-Za-z0-9_]{2,21}$")


class RedditSource:
    def __init__(self, name: str, subreddit: str, http: HttpClient, *, limit: int = 10) -> None:
        sub = subreddit.removeprefix("r/").removeprefix("/r/")
        if not _SUBREDDIT.fullmatch(sub):
            raise ValueError(f"source {name!r}: invalid subreddit {subreddit!r}")
        self.name = name
        self.subreddit = sub
        self.limit = limit
        self._http = http

    @property
    def url(self) -> str:
        return f"https://www.reddit.com/r/{self.subreddit}/top.json?t=day&limit={self.limit}"

    def collect(self) -> list[Item]:
        payload = self._http.get_json(self.url)
        try:
            children = payload["data"]["children"]
        except (KeyError, TypeError) as exc:
            raise ValueError(f"{self.name}: unexpected Reddit response") from exc
        items: list[Item] = []
        for child in children if isinstance(children, list) else []:
            post = child.get("data") if isinstance(child, dict) else None
            if not isinstance(post, dict) or post.get("stickied") or post.get("over_18"):
                continue
            title = clean_title(str(post.get("title") or ""))
            permalink = post.get("permalink")
            if not title or not isinstance(permalink, str) or not permalink.startswith("/r/"):
                continue
            items.append(
                Item(
                    title=title,
                    url="https://www.reddit.com" + permalink,
                    source=self.name,
                    published=from_epoch(post.get("created_utc")),
                    snippet=clean_snippet(str(post.get("selftext") or "")),
                    extra={
                        "score": post.get("score"),
                        "comments": post.get("num_comments"),
                        "link": post.get("url"),
                    },
                )
            )
        return newest_first(items)[: self.limit]


def build_reddit(spec: dict[str, Any], http: HttpClient, clock: Clock) -> RedditSource:
    del clock
    return RedditSource(
        spec_str(spec, "name"),
        spec_str(spec, "subreddit"),
        http,
        limit=spec_int(spec, "limit", 10),
    )
