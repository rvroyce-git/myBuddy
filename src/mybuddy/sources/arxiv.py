"""arXiv search via the export API (Atom)."""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any
from urllib.parse import urlencode

from mybuddy.http import HttpError
from mybuddy.models import Clock, HttpClient, Item
from mybuddy.sources._common import newest_first, spec_int, spec_str
from mybuddy.sources.rss import FEED_ACCEPT, parse_feed

ARXIV_API = "https://export.arxiv.org/api/query"
# arXiv's API is often slow or briefly overloaded; wait and try again before giving up.
RETRY_WAITS_SECONDS = (5.0, 15.0)


def _retryable(exc: HttpError) -> bool:
    return exc.status is None or exc.status == 429 or exc.status >= 500


def arxiv_url(query: str, max_results: int) -> str:
    params = {
        "search_query": query,
        "sortBy": "submittedDate",
        "sortOrder": "descending",
        "max_results": str(max_results),
    }
    return ARXIV_API + "?" + urlencode(params)


class ArxivSource:
    """Newest arXiv submissions matching an arXiv query such as ``cat:cs.CL``."""

    def __init__(
        self,
        name: str,
        query: str,
        http: HttpClient,
        *,
        limit: int = 10,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.name = name
        self.query = query
        self.limit = limit
        self._http = http
        self._sleep = sleep

    def _fetch(self) -> bytes:
        url = arxiv_url(self.query, self.limit)
        for wait in (*RETRY_WAITS_SECONDS, None):
            try:
                return self._http.get_bytes(url, accept=FEED_ACCEPT)
            except HttpError as exc:
                if wait is None or not _retryable(exc):
                    raise
                self._sleep(wait)
        raise AssertionError("unreachable")  # pragma: no cover

    def collect(self) -> list[Item]:
        data = self._fetch()
        items = []
        for it in parse_feed(data, self.name):
            url = it.url
            if url.startswith("http://arxiv.org/"):
                url = "https://" + url[len("http://") :]
            items.append(
                Item(
                    title=it.title,
                    url=url,
                    source=it.source,
                    published=it.published,
                    snippet=it.snippet,
                )
            )
        return newest_first(items)[: self.limit]


def build_arxiv(spec: dict[str, Any], http: HttpClient, clock: Clock) -> ArxivSource:
    del clock  # arXiv results are already the newest submissions
    return ArxivSource(
        spec_str(spec, "name"), spec_str(spec, "query"), http, limit=spec_int(spec, "limit", 10)
    )
