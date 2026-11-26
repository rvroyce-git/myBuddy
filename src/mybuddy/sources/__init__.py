"""News sources. ``build_source`` turns one ``sources:`` entry of the config into a Source."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from mybuddy.http import url_host
from mybuddy.models import Clock, HttpClient, Source
from mybuddy.sources.arxiv import ARXIV_API, build_arxiv
from mybuddy.sources.cisa_kev import KEV_URL, build_cisa_kev
from mybuddy.sources.federal_register import FR_API, build_federal_register
from mybuddy.sources.github_trending import SEARCH_API, build_github_trending
from mybuddy.sources.hackernews import HN_API, build_hackernews
from mybuddy.sources.huggingface import (
    DAILY_PAPERS_API,
    MODELS_API,
    build_hf_models,
    build_hf_papers,
)
from mybuddy.sources.recalls import (
    CPSC_API,
    OPENFDA_API,
    build_cpsc_recalls,
    build_openfda_recalls,
)
from mybuddy.sources.reddit import REDDIT_BASE, build_reddit
from mybuddy.sources.rss import GOOGLE_NEWS_RSS, build_google_news, build_rss

SourceBuilder = Callable[[dict[str, Any], HttpClient, Clock], Source]

SOURCE_TYPES: dict[str, SourceBuilder] = {
    "rss": build_rss,
    "google_news": build_google_news,
    "hackernews": build_hackernews,
    "reddit": build_reddit,
    "github_trending": build_github_trending,
    "arxiv": build_arxiv,
    "hf_papers": build_hf_papers,
    "hf_models": build_hf_models,
    "cisa_kev": build_cisa_kev,
    "federal_register": build_federal_register,
    "openfda_recalls": build_openfda_recalls,
    "cpsc_recalls": build_cpsc_recalls,
}


def _hosts(*urls: str) -> tuple[str, ...]:
    return tuple(sorted({h for h in map(url_host, urls) if h}))


# Fixed API hosts each type fetches from. The HTTP client's allowlist is built from these
# plus the hosts of URLs written in the config. Item links are only displayed, never fetched.
SOURCE_HOSTS: dict[str, tuple[str, ...]] = {
    "rss": (),  # only the feed URL from the config
    "google_news": _hosts(GOOGLE_NEWS_RSS),
    "hackernews": _hosts(HN_API),
    "reddit": _hosts(REDDIT_BASE),
    "github_trending": _hosts(SEARCH_API),
    "arxiv": _hosts(ARXIV_API),
    "hf_papers": _hosts(DAILY_PAPERS_API),
    "hf_models": _hosts(MODELS_API),
    "cisa_kev": _hosts(KEV_URL),
    "federal_register": _hosts(FR_API),
    "openfda_recalls": _hosts(OPENFDA_API),
    "cpsc_recalls": _hosts(CPSC_API),
}

# Keys each type must have besides ``type`` and ``name``.
REQUIRED_KEYS: dict[str, tuple[str, ...]] = {
    "rss": ("url",),
    "google_news": ("query",),
    "hackernews": ("keywords",),
    "reddit": ("subreddit",),
    "github_trending": ("topics",),
    "arxiv": ("query",),
    "hf_papers": (),
    "hf_models": (),
    "cisa_kev": (),
    "federal_register": ("term",),
    "openfda_recalls": ("category",),
    "cpsc_recalls": (),
}


def source_hosts(spec: dict[str, Any]) -> set[str]:
    """Hosts one config source entry needs: its type's fixed API hosts plus its own `url`."""
    hosts = set(SOURCE_HOSTS.get(str(spec.get("type")), ()))
    url = spec.get("url")
    host = url_host(url) if isinstance(url, str) else None
    if host:
        hosts.add(host)
    return hosts


def build_source(spec: dict[str, Any], http: HttpClient, clock: Clock) -> Source:
    """Build a Source from a config spec. Raises ValueError for any invalid spec."""
    if not isinstance(spec, dict):
        raise ValueError(f"source spec must be a mapping, got {type(spec).__name__}")
    kind = spec.get("type")
    name = spec.get("name")
    if not isinstance(name, str) or not name.strip():
        raise ValueError(f"source spec {spec!r} is missing a non-empty 'name'")
    if not isinstance(kind, str) or kind not in SOURCE_TYPES:
        known = ", ".join(sorted(SOURCE_TYPES))
        raise ValueError(f"source {name!r}: unknown type {kind!r} (expected one of: {known})")
    missing = [key for key in REQUIRED_KEYS[kind] if key not in spec]
    if missing:
        raise ValueError(
            f"source {name!r} (type {kind!r}) is missing required key(s): {', '.join(missing)}"
        )
    return SOURCE_TYPES[kind](spec, http, clock)


__all__ = [
    "REQUIRED_KEYS",
    "SOURCE_HOSTS",
    "SOURCE_TYPES",
    "SourceBuilder",
    "build_source",
    "source_hosts",
]
