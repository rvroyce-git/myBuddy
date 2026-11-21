from __future__ import annotations

import pytest
from test_sources_support import FakeHttp, FixedClock

from mybuddy.sources import REQUIRED_KEYS, SOURCE_TYPES, build_source

EXPECTED = {
    "rss",
    "google_news",
    "hackernews",
    "reddit",
    "github_trending",
    "arxiv",
    "hf_papers",
    "hf_models",
    "cisa_kev",
}

VALID = {
    "rss": {"url": "https://example.com/feed"},
    "google_news": {"query": "ai"},
    "hackernews": {"keywords": ["ai"]},
    "reddit": {"subreddit": "LocalLLaMA"},
    "github_trending": {"topics": ["llm"]},
    "arxiv": {"query": "cat:cs.AI"},
    "hf_papers": {},
    "hf_models": {},
    "cisa_kev": {},
}


def test_registry_has_every_contract_type() -> None:
    assert set(SOURCE_TYPES) == EXPECTED
    assert set(REQUIRED_KEYS) == EXPECTED


@pytest.mark.parametrize("kind", sorted(EXPECTED))
def test_build_each_type(kind: str) -> None:
    spec = {"type": kind, "name": f"My {kind}", **VALID[kind]}
    source = build_source(spec, FakeHttp(), FixedClock())
    assert source.name == f"My {kind}"
    assert callable(source.collect)


def test_unknown_type() -> None:
    with pytest.raises(ValueError, match="unknown type 'twitter'"):
        build_source({"type": "twitter", "name": "X"}, FakeHttp(), FixedClock())


def test_missing_type_and_name() -> None:
    with pytest.raises(ValueError, match="unknown type None"):
        build_source({"name": "X"}, FakeHttp(), FixedClock())
    with pytest.raises(ValueError, match="name"):
        build_source({"type": "rss", "url": "https://a.b/c"}, FakeHttp(), FixedClock())


@pytest.mark.parametrize("kind", [k for k in sorted(EXPECTED) if REQUIRED_KEYS[k]])
def test_missing_required_keys(kind: str) -> None:
    with pytest.raises(ValueError, match="missing required key"):
        build_source({"type": kind, "name": "S"}, FakeHttp(), FixedClock())


@pytest.mark.parametrize(
    "spec",
    [
        {"type": "rss", "name": "S", "url": "ftp://example.com/feed"},
        {"type": "rss", "name": "S", "url": "https://e.com/f", "limit": 0},
        {"type": "rss", "name": "S", "url": "https://e.com/f", "limit": "5"},
        {"type": "hackernews", "name": "S", "keywords": []},
        {"type": "hackernews", "name": "S", "keywords": [1]},
        {"type": "reddit", "name": "S", "subreddit": "bad/sub"},
        {"type": "github_trending", "name": "S", "topics": ["Bad Topic"]},
        {"type": "github_trending", "name": "S", "topics": ["llm"], "_state": [1]},
        {"type": "cisa_kev", "name": "S", "days": -1},
        {"type": "google_news", "name": "S", "query": "   "},
    ],
)
def test_invalid_values(spec: dict[str, object]) -> None:
    with pytest.raises(ValueError):
        build_source(spec, FakeHttp(), FixedClock())


def test_spec_not_a_mapping() -> None:
    with pytest.raises(ValueError, match="mapping"):
        build_source(["rss"], FakeHttp(), FixedClock())  # type: ignore[arg-type]
