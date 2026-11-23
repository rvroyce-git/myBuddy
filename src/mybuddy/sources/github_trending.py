"""Trending GitHub repositories via the (unauthenticated) search API.

Ranking: stars gained since the previous snapshot when one exists, otherwise total
stars of repositories pushed in the last 7 days. The snapshot is a mutable mapping
``full_name -> stars`` that the engine persists; it is updated in place.
"""

from __future__ import annotations

import re
import time
from collections.abc import Callable, MutableMapping
from datetime import timedelta
from typing import Any
from urllib.parse import quote_plus

from mybuddy.models import Clock, HttpClient, Item
from mybuddy.sources._common import (
    clean_snippet,
    clean_title,
    is_web_url,
    parse_iso,
    spec_int,
    spec_number,
    spec_str,
    spec_str_list,
)

SEARCH_API = "https://api.github.com/search/repositories"
_TOPIC = re.compile(r"^[a-z0-9][a-z0-9-]{0,49}$")


def search_url(topic: str, since: str, per_page: int = 30) -> str:
    query = quote_plus(f"topic:{topic} pushed:>={since}", safe=":>=")
    return f"{SEARCH_API}?q={query}&sort=stars&order=desc&per_page={per_page}"


class GitHubTrendingSource:
    def __init__(
        self,
        name: str,
        topics: list[str],
        http: HttpClient,
        clock: Clock,
        *,
        state: MutableMapping[str, int] | None = None,
        limit: int = 10,
        window_days: int = 7,
        rate_delay_seconds: float = 0,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        for topic in topics:
            if not _TOPIC.fullmatch(topic):
                raise ValueError(f"source {name!r}: invalid GitHub topic {topic!r}")
        self.name = name
        self.topics = topics
        self.state: MutableMapping[str, int] = state if state is not None else {}
        self.limit = limit
        self.window_days = window_days
        self.rate_delay_seconds = rate_delay_seconds
        self._http = http
        self._clock = clock
        self._sleep = sleep

    def collect(self) -> list[Item]:
        since = (self._clock.now() - timedelta(days=self.window_days)).date().isoformat()
        repos: dict[str, dict[str, Any]] = {}
        for index, topic in enumerate(self.topics):
            if index and self.rate_delay_seconds > 0:
                self._sleep(self.rate_delay_seconds)
            payload = self._http.get_json(search_url(topic, since))
            results = payload.get("items") if isinstance(payload, dict) else None
            if not isinstance(results, list):
                raise ValueError(f"{self.name}: unexpected GitHub search response for {topic!r}")
            for repo in results:
                if not isinstance(repo, dict):
                    continue
                full_name = repo.get("full_name")
                stars = repo.get("stargazers_count")
                if (
                    isinstance(full_name, str)
                    and isinstance(stars, int)
                    and not isinstance(stars, bool)
                    and is_web_url(repo.get("html_url"))
                ):
                    repos.setdefault(full_name, repo)

        has_snapshot = len(self.state) > 0
        ranked: list[tuple[Item, int, int | None]] = []
        for full_name, repo in repos.items():
            stars = int(repo["stargazers_count"])
            previous = self.state.get(full_name)
            if not isinstance(previous, int) or isinstance(previous, bool):
                previous = None  # missing or damaged snapshot value: treat as a newcomer
            gained = stars - previous if previous is not None else None
            description = str(repo.get("description") or "")
            language = repo.get("language")
            snippet = description
            if isinstance(language, str) and language:
                snippet = f"{description} ({language})" if description else language
            item = Item(
                title=clean_title(full_name),
                url=str(repo["html_url"]),
                source=self.name,
                published=parse_iso(repo.get("pushed_at")),
                snippet=clean_snippet(snippet),
                extra={"stars": stars, "stars_gained": gained},
            )
            ranked.append((item, stars, gained))

        if has_snapshot:
            # Repos with a baseline first (by gain), then newcomers by total stars.
            ranked.sort(key=lambda r: (r[2] is not None, r[2] or 0, r[1]), reverse=True)
        else:
            ranked.sort(key=lambda r: r[1], reverse=True)

        for full_name, repo in repos.items():
            self.state[full_name] = int(repo["stargazers_count"])
        return [item for item, _, _ in ranked[: self.limit]]


def build_github_trending(
    spec: dict[str, Any], http: HttpClient, clock: Clock
) -> GitHubTrendingSource:
    state = spec.get("_state")
    if state is not None and not isinstance(state, MutableMapping):
        raise ValueError(f"source {spec.get('name')!r}: '_state' must be a mutable mapping")
    return GitHubTrendingSource(
        spec_str(spec, "name"),
        spec_str_list(spec, "topics"),
        http,
        clock,
        state=state,
        limit=spec_int(spec, "limit", 10),
        window_days=spec_int(spec, "window_days", 7),
        rate_delay_seconds=spec_number(spec, "rate_delay_seconds", 0),
    )
