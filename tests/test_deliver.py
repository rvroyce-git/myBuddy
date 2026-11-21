from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest
from conftest import MONDAY, FakeHttp, FixedClock

from mybuddy.config import Settings
from mybuddy.deliver import (
    DeliveryError,
    DiscordWebhook,
    OutboxWriter,
    escape_md,
    format_report,
    make_deliverer,
    split_message,
)
from mybuddy.http import HttpError
from mybuddy.models import Report, ReportItem

HOOK = "https://discord.com/api/webhooks/1/abc"


def report(n: int = 2, **kw: object) -> Report:
    items = tuple(
        ReportItem(title=f"Item {i}", url=f"https://ex.com/{i}", source="Src", blurb=f"Blurb {i}.")
        for i in range(1, n + 1)
    )
    data: dict[str, object] = {
        "topic_id": "ai_news",
        "title": "AI news",
        "created_at": MONDAY,
        "headline": "Head.",
        "summary": "Sum.",
        "items": items,
    }
    data.update(kw)
    return Report(**data)  # type: ignore[arg-type]


def test_format_report_layout() -> None:
    text = format_report(report(), "🧠")
    lines = text.splitlines()
    assert lines[0] == "🧠 **AI news**"
    assert lines[1] == "**Head.**"
    assert "Sum." in lines
    assert "1. [Item 1](<https://ex.com/1>) — Src — Blurb 1." in lines


def test_escaping_and_link_safety() -> None:
    item = ReportItem(
        title="[x](https://evil) *bold* @everyone",
        url="https://ex.com/a b>c",
        source="S_r",
        blurb="new\nline",
    )
    text = format_report(report(items=(item,)), "🧠")
    assert "\\[x\\](https://evil)" in text and "\\*bold\\*" in text
    assert "@​everyone" in text
    assert "(<https://ex.com/a%20b%3Ec>)" in text
    assert "S\\_r" in text and "new line" in text
    assert escape_md("a|b`c") == "a\\|b\\`c"


def test_split_on_line_boundaries() -> None:
    lines = [f"line {i} " + "x" * 90 for i in range(60)]
    parts = split_message("\n".join(lines))
    assert len(parts) > 1 and all(len(p) <= 2000 for p in parts)
    assert "\n".join(parts).split("\n") == lines
    long = split_message("y" * 4500)
    assert [len(p) for p in long] == [2000, 2000, 500]


def test_webhook_posts_parts_without_mentions() -> None:
    http = FakeHttp()
    slept: list[float] = []
    DiscordWebhook(HOOK, http, sleep=slept.append).deliver(report(40), "🧠")
    posts = http.posts
    assert len(posts) >= 2 and len(slept) == len(posts) - 1
    for _, url, payload, _ in posts:
        assert url == HOOK
        assert payload["allowed_mentions"] == {"parse": []}
        assert len(payload["content"]) <= 2000


def test_webhook_429_retry_after() -> None:
    http = FakeHttp()
    body = json.dumps({"retry_after": 1.5})
    http.post_responses = [HttpError("rl", status=429, body=body), None]
    slept: list[float] = []
    DiscordWebhook(HOOK, http, sleep=slept.append).deliver(report(1), "🧠")
    assert slept == [1.5] and len(http.posts) == 2


def test_webhook_error_raises() -> None:
    http = FakeHttp()
    http.post_responses = [HttpError("bad", status=404)]
    with pytest.raises(DeliveryError, match="404"):
        DiscordWebhook(HOOK, http, sleep=lambda s: None).deliver(report(1), "🧠")
    with pytest.raises(DeliveryError):
        DiscordWebhook("http://insecure", http)


def test_outbox_and_factory(settings: Settings, data_dir: Path) -> None:
    clock = FixedClock(MONDAY)
    http = FakeHttp()
    d = make_deliverer(settings, http, clock)
    assert isinstance(d, OutboxWriter)
    d.deliver(report(), "🧠")
    d.deliver(report(), "🧠")
    files = sorted((data_dir / "outbox").iterdir())
    assert [f.name for f in files] == ["ai_news-20261123-063000-2.md", "ai_news-20261123-063000.md"]
    assert "**Head.**" in files[1].read_text(encoding="utf-8")
    assert http.calls == []
    real = replace(settings, discord_webhook_url=HOOK)
    assert isinstance(make_deliverer(real, http, clock), DiscordWebhook)
    assert isinstance(make_deliverer(replace(real, fake=True), http, clock), OutboxWriter)
