from __future__ import annotations

import json
from dataclasses import replace
from typing import Any

from conftest import FakeHttp, FixedClock, make_item, make_topic

from mybuddy.config import Settings
from mybuddy.http import HttpError
from mybuddy.summarize import GEMINI_URL, GROQ_URL, MAX_AI_ITEMS, Summarizer, parse_model_json


def chat(content: Any) -> dict[str, Any]:
    text = content if isinstance(content, str) else json.dumps(content)
    return {"choices": [{"message": {"content": text}}]}


def good_json(**kw: Any) -> dict[str, Any]:
    data: dict[str, Any] = {
        "headline": "Big model released.",
        "summary": "A lab shipped a model. It is open.",
        "blurbs": [{"index": 2, "text": "Second story matters."}, {"index": 1, "text": "First."}],
    }
    data.update(kw)
    return data


def make(settings: Settings, http: FakeHttp, clock: FixedClock) -> Summarizer:
    return Summarizer(settings, http, clock, sleep=lambda s: None)


def test_valid_json_builds_report(settings: Settings, http: FakeHttp, clock: FixedClock) -> None:
    items = [make_item(1), make_item(2), make_item(3)]
    http.post_responses = [chat(good_json())]
    report = make(settings, http, clock).summarize(make_topic(), items)
    assert report.headline == "Big model released."
    assert [i.url for i in report.items] == [items[1].url, items[0].url]
    assert report.items[0].blurb == "Second story matters."
    assert report.created_at == clock.now()

    [(_, url, payload, headers)] = http.posts
    assert url == GROQ_URL
    assert headers == {"Authorization": "Bearer gsk_test"}
    assert payload["response_format"] == {"type": "json_object"}
    assert payload["temperature"] == 0.3 and payload["model"] == "openai/gpt-oss-120b"
    system, user = payload["messages"]
    assert "never invent" in system["content"].lower()
    assert "[3] Story 3" in user["content"] and "Snippet for story 3." in user["content"]


def test_caps_items_sent(settings: Settings, http: FakeHttp, clock: FixedClock) -> None:
    items = [make_item(i, snippet="x" * 1000) for i in range(20)]
    http.post_responses = [chat(good_json())]
    make(settings, http, clock).summarize(make_topic(), items)
    user = http.posts[0][2]["messages"][1]["content"]
    assert f"[{MAX_AI_ITEMS}]" in user and f"[{MAX_AI_ITEMS + 1}]" not in user
    assert "x" * 300 not in user


def test_bad_index_and_urls_from_model_ignored(
    settings: Settings, http: FakeHttp, clock: FixedClock
) -> None:
    items = [make_item(1), make_item(2)]
    http.post_responses = [
        chat(
            good_json(
                headline="See https://evil.example/x now",
                blurbs=[
                    {"index": 7, "text": "out of range"},
                    {"index": 0, "text": "zero"},
                    {"index": True, "text": "bool"},
                    {"index": 1, "text": "Read www.evil.example and https://evil.example/y"},
                    {"index": 1, "text": "duplicate"},
                    {"index": 2, "url": "https://evil.example", "text": "fine"},
                    "junk",
                ],
            )
        )
    ]
    report = make(settings, http, clock).summarize(make_topic(), items)
    assert [i.url for i in report.items] == [items[0].url, items[1].url]
    assert "evil" not in report.headline
    assert all("evil" not in i.blurb and "evil" not in i.url for i in report.items)


def test_no_valid_blurbs_keeps_all_items(
    settings: Settings, http: FakeHttp, clock: FixedClock
) -> None:
    items = [make_item(1), make_item(2)]
    http.post_responses = [chat(good_json(blurbs=[{"index": 99, "text": "x"}]))]
    report = make(settings, http, clock).summarize(make_topic(), items)
    assert [i.blurb for i in report.items] == ["Snippet for story 1.", "Snippet for story 2."]


def test_invalid_json_falls_back(settings: Settings, http: FakeHttp, clock: FixedClock) -> None:
    items = [make_item(1, source="OpenAI"), make_item(2, source="HN")]
    http.post_responses = [chat("not json {")]
    report = make(settings, http, clock).summarize(make_topic(), items)
    assert report.headline == "Today's top items from OpenAI, HN"
    assert [i.blurb for i in report.items] == ["Snippet for story 1.", "Snippet for story 2."]


def test_groq_fails_then_gemini(settings: Settings, http: FakeHttp, clock: FixedClock) -> None:
    s = replace(settings, gemini_api_key="gem")
    http.post_responses = [HttpError("boom", status=500), chat(good_json(headline="From Gemini."))]
    report = make(s, http, clock).summarize(make_topic(), [make_item(1), make_item(2)])
    assert report.headline == "From Gemini."
    assert [p[1] for p in http.posts] == [GROQ_URL, GEMINI_URL]
    assert http.posts[1][2]["model"] == "gemini-2.5-flash"


def test_all_providers_fail_fallback(settings: Settings, http: FakeHttp, clock: FixedClock) -> None:
    s = replace(settings, gemini_api_key="gem")
    http.post_responses = [HttpError("x", status=500), {"unexpected": True}]
    report = make(s, http, clock).summarize(make_topic(), [make_item(1)])
    assert report.headline.startswith("Today's top items from")


def test_429_retried_once(settings: Settings, http: FakeHttp, clock: FixedClock) -> None:
    slept: list[float] = []
    http.post_responses = [HttpError("slow", status=429, retry_after=3), chat(good_json())]
    s = Summarizer(settings, http, clock, sleep=slept.append)
    report = s.summarize(make_topic(), [make_item(1), make_item(2)])
    assert report.headline == "Big model released." and slept == [3]


def test_no_keys_means_fallback_without_network(
    settings: Settings, http: FakeHttp, clock: FixedClock
) -> None:
    s = replace(settings, groq_api_key=None)
    report = make(s, http, clock).summarize(make_topic(), [make_item(1)])
    assert report.headline.startswith("Today's top items") and http.calls == []


def test_fake_mode_is_deterministic_and_offline(
    settings: Settings, http: FakeHttp, clock: FixedClock
) -> None:
    s = make(replace(settings, fake=True), http, clock)
    items = [make_item(1), make_item(2, snippet="")]
    a = s.summarize(make_topic(), items, title="AI news: today")
    b = s.summarize(make_topic(), items, title="AI news: today")
    assert a == b and http.calls == []
    assert a.title == "AI news: today" and a.headline.startswith("(fake) 2 new items")
    assert a.items[1].blurb == "Item 2 from Feed."


def test_parse_model_json_accepts_code_fence() -> None:
    data = parse_model_json('```json\n{"headline": "h", "summary": "s", "blurbs": []}\n```')
    assert data["headline"] == "h"


def test_provider_error_body_is_logged(
    settings: Settings, http: FakeHttp, clock: FixedClock
) -> None:
    import io

    log = io.StringIO()
    http.post_responses = [
        HttpError("HTTP 400", status=400, body='{"error": "json_validate_failed"}')
    ]
    s = Summarizer(settings, http, clock, sleep=lambda s: None, log=log)
    report = s.summarize(make_topic(), [make_item(1)])
    assert "json_validate_failed" in log.getvalue()
    assert report.headline.startswith("Today's top items from")
    assert http.posts[0][2]["max_tokens"] >= 1500  # room for reasoning models
