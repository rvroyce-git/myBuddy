from __future__ import annotations

from typing import Any

import pytest
from test_sources_support import FakeHttp, FixedClock, fixture_json

from mybuddy.sources import build_source
from mybuddy.sources.github_trending import GitHubTrendingSource, search_url

LLM_URL = (
    "https://api.github.com/search/repositories?q=topic:llm+pushed:>=2026-11-15"
    "&sort=stars&order=desc&per_page=30"
)
AGENTS_URL = LLM_URL.replace("topic:llm", "topic:ai-agents")


def agents_payload() -> dict[str, Any]:
    llm = fixture_json("github_search_llm.json")
    return {
        "items": [
            llm["items"][1],  # duplicate across topics
            {
                "full_name": "someone/small-agent",
                "html_url": "https://github.com/someone/small-agent",
                "description": "<b>Tiny</b> agent",
                "stargazers_count": 1500,
                "pushed_at": "2026-11-21T10:00:00Z",
                "language": None,
            },
            {"full_name": "broken/no-url", "stargazers_count": 5},
        ]
    }


def make(state: dict[str, int] | None, **spec: Any) -> tuple[Any, FakeHttp]:
    http = FakeHttp({LLM_URL: fixture_json("github_search_llm.json"), AGENTS_URL: agents_payload()})
    full: dict[str, Any] = {
        "type": "github_trending",
        "name": "GitHub Trending",
        "topics": ["llm", "ai-agents"],
        **spec,
    }
    if state is not None:
        full["_state"] = state
    return build_source(full, http, FixedClock()), http


def test_search_url() -> None:
    assert search_url("llm", "2026-11-15") == LLM_URL


def test_first_run_ranks_by_stars_and_fills_state() -> None:
    state: dict[str, int] = {}
    source, http = make(state)
    items = source.collect()
    assert http.calls == [LLM_URL, AGENTS_URL]
    assert [it.title for it in items] == [
        "affaan-m/ECC",
        "NousResearch/hermes-agent",
        "firecrawl/firecrawl",
        "Significant-Gravitas/AutoGPT",
        "someone/small-agent",
    ]
    assert items[0].extra == {"stars": 308333, "stars_gained": None}
    assert items[0].url == "https://github.com/affaan-m/ECC"
    assert items[0].published is not None
    assert "(JavaScript)" in items[0].snippet
    assert items[4].snippet == "Tiny agent"
    assert state == {
        "affaan-m/ECC": 308333,
        "NousResearch/hermes-agent": 265186,
        "firecrawl/firecrawl": 206109,
        "Significant-Gravitas/AutoGPT": 188127,
        "someone/small-agent": 1500,
    }


def test_ranks_by_stars_gained_with_snapshot() -> None:
    state = {
        "affaan-m/ECC": 308300,  # +33
        "NousResearch/hermes-agent": 264186,  # +1000
        "firecrawl/firecrawl": 206109,  # +0
        "someone/small-agent": 1000,  # +500
        "old/gone": 7,
    }
    source, _ = make(state)
    items = source.collect()
    assert [(it.title, it.extra["stars_gained"]) for it in items] == [
        ("NousResearch/hermes-agent", 1000),
        ("someone/small-agent", 500),
        ("affaan-m/ECC", 33),
        ("firecrawl/firecrawl", 0),
        ("Significant-Gravitas/AutoGPT", None),  # new: no baseline yet
    ]
    assert state["NousResearch/hermes-agent"] == 265186
    assert state["Significant-Gravitas/AutoGPT"] == 188127
    assert state["old/gone"] == 7  # untouched


def test_limit_and_works_without_state_key() -> None:
    source, _ = make(None, limit=2)
    assert [it.title for it in source.collect()] == ["affaan-m/ECC", "NousResearch/hermes-agent"]


def test_rate_delay_uses_injected_sleep() -> None:
    sleeps: list[float] = []
    http = FakeHttp({LLM_URL: fixture_json("github_search_llm.json"), AGENTS_URL: agents_payload()})
    source = GitHubTrendingSource(
        "GH",
        ["llm", "ai-agents"],
        http,
        FixedClock(),
        rate_delay_seconds=6.5,
        sleep=sleeps.append,
    )
    source.collect()
    assert sleeps == [6.5]


def test_bad_response_raises() -> None:
    http = FakeHttp({LLM_URL: {"message": "API rate limit exceeded"}})
    source = GitHubTrendingSource("GH", ["llm"], http, FixedClock())
    with pytest.raises(ValueError, match="unexpected"):
        source.collect()
