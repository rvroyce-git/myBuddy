"""News sources. ``build_source`` turns one ``sources:`` entry of the config into a Source."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from mybuddy.models import Clock, HttpClient, Source
from mybuddy.sources.arxiv import build_arxiv
from mybuddy.sources.cisa_kev import build_cisa_kev
from mybuddy.sources.github_trending import build_github_trending
from mybuddy.sources.hackernews import build_hackernews
from mybuddy.sources.huggingface import build_hf_models, build_hf_papers
from mybuddy.sources.reddit import build_reddit
from mybuddy.sources.rss import build_google_news, build_rss

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
}


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


__all__ = ["REQUIRED_KEYS", "SOURCE_TYPES", "SourceBuilder", "build_source"]
