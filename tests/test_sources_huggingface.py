from __future__ import annotations

from datetime import UTC, datetime

from test_sources_support import FakeHttp, FixedClock, fixture_json

from mybuddy.sources import build_source

PAPERS = "https://huggingface.co/api/daily_papers"
MODELS = "https://huggingface.co/api/models?sort=trendingScore*"


def papers(data: object, **spec: object) -> list:  # type: ignore[type-arg]
    full = {"type": "hf_papers", "name": "HF Daily Papers", **spec}
    return build_source(full, FakeHttp({PAPERS: data}), FixedClock()).collect()


def models(data: object, **spec: object) -> list:  # type: ignore[type-arg]
    full = {"type": "hf_models", "name": "HF Trending Models", **spec}
    return build_source(full, FakeHttp({MODELS: data}), FixedClock()).collect()


def test_papers_fixture_newest_then_upvotes() -> None:
    items = papers(fixture_json("hf_daily_papers.json"))
    assert [it.url.rsplit("/", 1)[1] for it in items] == [
        "2611.26239",
        "2611.25604",
        "2611.26232",
        "2611.25200",
        "2611.15941",
    ]
    first = items[0]
    assert first.url == "https://huggingface.co/papers/2611.26239"
    assert first.published == datetime(2026, 11, 18, 19, tzinfo=UTC)
    assert first.extra["upvotes"] == 5
    assert 0 < len(first.snippet) <= 300
    assert len(papers(fixture_json("hf_daily_papers.json"), limit=2)) == 2


def test_papers_skip_bad() -> None:
    data = [
        {"paper": {"id": "../evil", "title": "x"}},
        {"paper": {"id": "2611.00001", "title": ""}},
        {"paper": {"id": "2611.00002", "title": "Good"}, "publishedAt": "2026-11-20T00:00:00Z"},
        {"paper": {"id": "2611.00002", "title": "Dup"}},
        {"nopaper": 1},
    ]
    items = papers(data)
    assert [it.title for it in items] == ["Good"]


def test_models_fixture_keeps_trending_order() -> None:
    http = FakeHttp({MODELS: fixture_json("hf_models.json")})
    spec = {"type": "hf_models", "name": "HF Trending Models", "limit": 3}
    items = build_source(spec, http, FixedClock()).collect()
    assert http.calls == ["https://huggingface.co/api/models?sort=trendingScore&limit=6"]
    assert [it.title for it in items] == [
        "thinkingmachines/Inkling-2-Mini",
        "unsloth/Inkling-2-Mini-GGUF",
        "tencent/Hy4-flash",
    ]
    first = items[0]
    assert first.url == "https://huggingface.co/thinkingmachines/Inkling-2-Mini"
    assert first.snippet.startswith("image-text-to-text · ")
    assert "downloads" in first.snippet and "likes" in first.snippet
    assert first.extra["likes"] == 2445
    assert first.published is not None and first.published.tzinfo == UTC


def test_models_skip_bad() -> None:
    data = [
        {"id": "bad id with spaces"},
        {"modelId": "org/private", "private": True},
        {"modelId": "org/ok", "downloads": 1200, "likes": 3},
    ]
    items = models(data)
    assert [it.title for it in items] == ["org/ok"]
    assert items[0].snippet == "1,200 downloads · 3 likes"
