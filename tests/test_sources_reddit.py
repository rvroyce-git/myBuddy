from __future__ import annotations

from datetime import UTC, datetime

import pytest
from test_sources_support import FakeHttp, FixedClock, fixture_json

from mybuddy.sources import build_source

URL = "https://www.reddit.com/r/LocalLLaMA/top.json?t=day&limit=10"


def source(http: FakeHttp, **extra: object):  # type: ignore[no-untyped-def]
    spec = {"type": "reddit", "name": "r/LocalLLaMA", "subreddit": "LocalLLaMA", **extra}
    return build_source(spec, http, FixedClock())


def test_parses_listing_skips_bad_newest_first() -> None:
    http = FakeHttp({URL: fixture_json("reddit_top.json")})
    items = source(http).collect()
    assert http.calls == [URL]
    assert [it.title for it in items] == [
        "New open-weights 70B model beats last month's frontier on coding evals",
        "Inkling-2-Mini runs at 40 tok/s on a single 3090",
        "Benchmarking speculative decoding on Apple silicon",
    ]
    top = items[1]
    assert top.url.startswith("https://www.reddit.com/r/LocalLLaMA/comments/1p2bb02/")
    assert top.published == datetime(2026, 11, 21, 12, 0, tzinfo=UTC)
    assert top.snippet == "Quantised to **4-bit** with & llama.cpp. Details in comments."
    assert top.extra["score"] == 1830


def test_limit_in_url_and_result() -> None:
    url = "https://www.reddit.com/r/LocalLLaMA/top.json?t=day&limit=2"
    items = source(FakeHttp({url: fixture_json("reddit_top.json")}), limit=2).collect()
    assert len(items) == 2


def test_blocked_raises() -> None:
    with pytest.raises(PermissionError):
        source(FakeHttp({URL: PermissionError("HTTP 403")})).collect()
    with pytest.raises(ValueError):
        source(FakeHttp({URL: {"error": 429}})).collect()


def test_accepts_r_prefix() -> None:
    http = FakeHttp({URL: fixture_json("reddit_top.json")})
    assert len(source(http, subreddit="r/LocalLLaMA").collect()) == 3
