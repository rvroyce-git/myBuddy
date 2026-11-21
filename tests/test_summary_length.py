"""The website cards and Discord posts must stay short, whatever the model or feed sends."""

from __future__ import annotations

import json
from dataclasses import replace
from typing import Any

from conftest import FakeHttp, FixedClock, make_item, make_topic

from mybuddy.config import Settings
from mybuddy.summarize import (
    BLURB_MAX,
    HEADLINE_MAX,
    REPORT_ITEMS_MAX,
    SUMMARY_MAX,
    Summarizer,
    first_sentences,
    snippet_blurb,
)


def chat(data: dict[str, Any]) -> dict[str, Any]:
    return {"choices": [{"message": {"content": json.dumps(data)}}]}


def make(settings: Settings, http: FakeHttp, clock: FixedClock) -> Summarizer:
    return Summarizer(settings, http, clock, sleep=lambda s: None)


def test_first_sentences() -> None:
    text = "One thing happened. Two more did! Three? Four."
    assert first_sentences(text, 1) == "One thing happened."
    assert first_sentences(text, 2) == "One thing happened. Two more did!"
    assert first_sentences("No end mark here", 2) == "No end mark here"
    # Decimal points and versions are not sentence breaks.
    assert first_sentences("Version 3.4.2 fixes it. Update now.", 1) == "Version 3.4.2 fixes it."


def test_long_model_output_is_trimmed(
    settings: Settings, http: FakeHttp, clock: FixedClock
) -> None:
    items = [make_item(i) for i in range(1, 12)]
    http.post_responses = [
        chat(
            {
                "headline": "A " + "very " * 60 + "long headline.",
                "summary": "First sentence here. Second sentence here. Third is cut. Fourth too.",
                "blurbs": [{"index": i, "text": "word " * 80} for i in range(1, 12)],
            }
        )
    ]
    report = make(settings, http, clock).summarize(make_topic(), items)
    assert len(report.headline) <= HEADLINE_MAX
    assert report.summary == "First sentence here. Second sentence here."
    assert len(report.summary) <= SUMMARY_MAX
    assert len(report.items) == REPORT_ITEMS_MAX
    assert all(len(i.blurb) <= BLURB_MAX for i in report.items)


def test_prompt_asks_for_short_output(
    settings: Settings, http: FakeHttp, clock: FixedClock
) -> None:
    http.post_responses = [chat({"headline": "H.", "summary": "S.", "blurbs": []})]
    make(settings, http, clock).summarize(make_topic(), [make_item(1)])
    system, user = http.posts[0][2]["messages"]
    assert "at most 14 words" in system["content"]
    assert "max 35 words" in user["content"]
    assert f"at most {REPORT_ITEMS_MAX} blurbs" in user["content"]


def test_snippet_blurb_drops_feed_boilerplate() -> None:
    item = make_item(
        1,
        snippet="Microsoft patched a bug in Entra. Attackers used it to read admin emails. [...]",
    )
    assert snippet_blurb(item) == "Microsoft patched a bug in Entra."
    item = make_item(2, snippet="A new tool ships today \\[…\\]")
    assert snippet_blurb(item) == "A new tool ships today"
    item = make_item(3, snippet="Big launch. The post Big launch appeared first on Example News.")
    assert snippet_blurb(item) == "Big launch."
    assert snippet_blurb(make_item(4, snippet="")) == ""
    assert len(snippet_blurb(make_item(5, snippet="word " * 100))) <= BLURB_MAX


def test_fallback_and_fake_reports_are_short(
    settings: Settings, http: FakeHttp, clock: FixedClock
) -> None:
    items = [make_item(i, snippet="Long sentence one. Long sentence two.") for i in range(1, 12)]
    no_keys = replace(settings, groq_api_key=None, gemini_api_key=None)
    report = make(no_keys, http, clock).summarize(make_topic(), items)
    assert len(report.items) == REPORT_ITEMS_MAX
    assert all(i.blurb == "Long sentence one." for i in report.items)
    fake = make(settings, http, clock).summarize(make_topic(), items, fake=True)
    assert len(fake.items) == REPORT_ITEMS_MAX
