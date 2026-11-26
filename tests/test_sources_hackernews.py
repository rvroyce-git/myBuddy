from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from test_sources_support import FakeHttp, FixedClock, fixture_json

from mybuddy.sources import build_source

API = "https://hacker-news.firebaseio.com/v0"


def routes(extra: dict[str, Any] | None = None) -> dict[str, Any]:
    data = fixture_json("hackernews.json")
    out: dict[str, Any] = {f"{API}/topstories.json": data["topstories"]}
    for key, item in data["items"].items():
        out[f"{API}/item/{key}.json"] = item
    out.update(extra or {})
    return out


def collect(r: dict[str, Any], **spec: Any) -> list[Any]:
    full = {"type": "hackernews", "name": "Hacker News", "keywords": ["lean", "inkling"], **spec}
    return build_source(full, FakeHttp(r), FixedClock()).collect()


def test_keywords_filter_and_newest_first() -> None:
    items = collect(routes())
    assert [it.title for it in items] == [
        "Inkling-2-Mini in 5.4 GB Memory",
        "Lean verification of the Riemann Hypothesis",
    ]
    first = items[0]
    assert first.url == "https://github.com/ShinMegamiBoson/lookatmyquant"
    assert first.published == datetime(2026, 11, 21, 18, 2, 19, tzinfo=UTC)
    assert first.extra["score"] == 277
    assert first.extra["discussion_url"] == "https://news.ycombinator.com/item?id=50591853"


def test_keyword_is_whole_word_case_insensitive() -> None:
    assert collect(routes(), keywords=["LEAN"])[0].title.startswith("Lean")
    assert collect(routes(), keywords=["lea"]) == []


def test_min_score_limit_and_age() -> None:
    kw = ["lean", "inkling", "pill", "lead"]
    assert len(collect(routes(), keywords=kw)) == 4
    assert [it.extra["score"] for it in collect(routes(), keywords=kw, min_score=400)] == [689, 457]
    assert len(collect(routes(), keywords=kw, limit=1)) == 1
    # pancreatic-cancer story is from 2026-11-20 21:03 UTC, 27 h before the clock
    assert len(collect(routes(), keywords=kw, max_age_hours=24)) == 3


def test_skips_bad_and_missing_items() -> None:
    extra = {
        f"{API}/topstories.json": [1, 2, 3, 4, "x", 50591853, 999],
        f"{API}/item/1.json": {"id": 1, "type": "comment", "title": "lean", "score": 999},
        f"{API}/item/2.json": {
            "id": 2,
            "type": "story",
            "score": 999,
            "dead": True,
            "title": "lean",
        },
        f"{API}/item/3.json": {"id": 3, "type": "story", "score": 999, "title": ""},
        f"{API}/item/4.json": {
            "id": 4,
            "type": "story",
            "score": 120,
            "time": 1795280000,
            "title": "Ask HN: Lean or Coq?",
            "text": "<p>Which one &amp; why?</p>",
        },
        # 999 has no route -> fetch error -> skipped
    }
    items = collect(routes(extra))
    assert [it.title for it in items] == ["Inkling-2-Mini in 5.4 GB Memory", "Ask HN: Lean or Coq?"]
    ask = items[1]
    assert ask.url == "https://news.ycombinator.com/item?id=4"
    assert ask.snippet == "Which one & why?"
