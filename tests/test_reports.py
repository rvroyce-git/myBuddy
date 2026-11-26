"""Telegram-only reports: the `website`/`kind` config flags, morning digest, weekly review,
health check, run/failure log and schedules."""

from __future__ import annotations

import io
from collections.abc import Iterator
from dataclasses import replace
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from conftest import FakeHttp, FakeSource, FixedClock, make_item, make_topic

from mybuddy import cli
from mybuddy.config import WEEKDAYS, ConfigError, Schedule, Settings, Topic, parse_topics
from mybuddy.deliver import OutboxSubscribers, format_report, format_telegram
from mybuddy.engine import Deps, run_topic
from mybuddy.export import build_export, report_to_dict
from mybuddy.models import Clock, HttpClient, Report, ReportItem, Source
from mybuddy.reports import (
    collect_health,
    digest_items,
    health_report,
    stale_after,
    weekly_items,
)
from mybuddy.schedule import IST, next_run
from mybuddy.store import Store
from mybuddy.subscriptions import audience_map, topic_bits, website_topics
from mybuddy.summarize import Summarizer

THURSDAY = datetime(2026, 11, 26, 8, 30, tzinfo=IST)


def _yaml_topic(**kw: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "id": "ai_news",
        "title": "AI news",
        "emoji": "🧠",
        "description": "d",
        "schedule": {"time": "06:30"},
        "ai": True,
        "bit": 0,
        "prompt": "ai_news.md",
        "sources": [{"type": "rss", "name": "A", "url": "https://a.example/feed"}],
    }
    base.update(kw)
    return {k: v for k, v in base.items() if v is not None}


def _parse(*topics: dict[str, Any]) -> list[Topic]:
    return parse_topics({"topics": list(topics)}, source_types={"rss"})


DIGEST = {"kind": "digest", "website": False, "bit": None, "sources": None}
HEALTH = {
    "kind": "health",
    "ai": False,
    "website": False,
    "bit": None,
    "prompt": None,
    "sources": None,
}


# ------------------------------------------------------------------------ config


def test_website_flag_defaults_to_old_behaviour() -> None:
    news, kev = _parse(
        _yaml_topic(),
        _yaml_topic(id="kev", ai=False, bit=None, prompt=None, audience="ai_news"),
    )
    assert news.on_website and news.website is True and not kev.on_website
    # Topics built in code without the flag keep the old rule too.
    assert make_topic().on_website and not make_topic(ai=False).on_website
    assert not make_topic(website=False).on_website


def test_ai_topic_off_website_needs_no_bit() -> None:
    topics = _parse(_yaml_topic(), _yaml_topic(id="policy", website=False, bit=None))
    policy = topics[1]
    assert policy.ai and not policy.on_website and policy.bit is None
    assert [t.id for t in website_topics(topics)] == ["ai_news"]
    assert topic_bits(topics) == {"ai_news": 0}
    assert "policy" not in audience_map(topics)  # owner only without an audience


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"website": "no"}, "website: must be true or false"),
        ({"ai": False, "prompt": None, "bit": None, "website": True}, "only AI topics"),
        ({"website": False}, "only AI website topics have a subscription bit"),
        ({"kind": "markets"}, "kind: must be one of"),
        ({**DIGEST, "website": None, "bit": 5}, "Telegram-only; set website: false"),
        ({**DIGEST, "sources": [{"type": "rss", "name": "A", "url": "https://a/b"}]}, "remove"),
        ({**DIGEST, "ai": False, "prompt": None}, "need ai: true"),
        ({**HEALTH, "ai": True, "prompt": "ai_news.md"}, "do not use AI"),
        ({**HEALTH, "audience": "ai_news"}, "owner only"),
        ({"website": False, "bit": None, "audience": "nope"}, "id of a website topic"),
        ({"website": False, "bit": None, "dedupe_with": "ai_news"}, "list of topic ids"),
        ({"website": False, "bit": None, "dedupe_with": ["nope"]}, "another topic"),
        ({**DIGEST, "dedupe_with": ["ai_news"]}, "only news topics"),
    ],
)
def test_bad_new_flags_rejected(change: dict[str, Any], message: str) -> None:
    with pytest.raises(ConfigError, match=message):
        _parse(_yaml_topic(), _yaml_topic(id="other", **change))


def test_compiled_kinds_parse_without_sources() -> None:
    _, digest, health = _parse(
        _yaml_topic(), _yaml_topic(id="d", **DIGEST), _yaml_topic(id="h", **HEALTH)
    )
    assert digest.kind == "digest" and digest.sources == () and not digest.on_website
    assert health.kind == "health" and not health.ai and health.audience is None


# ------------------------------------------------------------------------ export


def test_export_excludes_website_false_topics(tmp_path: Path) -> None:
    topics = [
        make_topic(),
        make_topic(id="policy", website=False, bit=None),
        make_topic(id="digest", kind="digest", website=False, bit=None, sources=()),
    ]
    with Store(tmp_path) as store:
        data = build_export(topics, store, THURSDAY)
    assert [t["id"] for t in data["topics"]] == ["ai_news"]


# ------------------------------------------------------------- digest and weekly


def _report(topic_id: str, when: datetime, n: int, headline: str = "") -> dict[str, Any]:
    items = [make_item(k, source=f"{topic_id} feed") for k in range(n)]
    return report_to_dict(
        Report(
            topic_id=topic_id,
            title=topic_id,
            created_at=when,
            headline=headline or f"{topic_id} headline",
            summary="s",
            items=tuple(
                ReportItem(i.title, i.url, i.source, f"blurb {k}", i.published)
                for k, i in enumerate(items)
            ),
        )
    )


TOPICS = [
    make_topic(id="ai_news", title="AI news"),
    make_topic(id="security", title="Security", bit=1),
    make_topic(
        id="morning_digest",
        title="Morning digest",
        kind="digest",
        website=False,
        bit=None,
        sources=(),
        prompt="morning_digest.md",
    ),
    make_topic(
        id="weekly_review",
        title="Weekly review",
        kind="weekly",
        website=False,
        bit=None,
        sources=(),
        prompt="weekly_review.md",
        schedule=Schedule(time="10:00", days=("sun",)),
    ),
    make_topic(
        id="health_check",
        title="Health check",
        kind="health",
        ai=False,
        website=False,
        sources=(),
        prompt=None,
        schedule=Schedule(time="09:00"),
    ),
]


@pytest.fixture
def store(tmp_path: Path) -> Iterator[Store]:
    with Store(tmp_path / "data") as s:
        yield s


def test_digest_uses_only_todays_latest_reports(store: Store) -> None:
    yesterday = THURSDAY - timedelta(days=1)
    store.save_report("ai_news", yesterday, _report("ai_news_old", yesterday, 3))
    store.save_report("ai_news", THURSDAY - timedelta(hours=2), _report("ai_news", THURSDAY, 4))
    store.save_report("security", THURSDAY - timedelta(hours=1), _report("security", THURSDAY, 1))
    # Its own and non-news reports never feed the digest.
    store.save_report("morning_digest", THURSDAY, _report("morning_digest", THURSDAY, 2))
    items = digest_items(TOPICS, store, THURSDAY, "morning_digest")
    assert [i.url for i in items] == [
        "https://example.com/ai_news-feed/0",
        "https://example.com/security-feed/0",
        "https://example.com/ai_news-feed/1",
    ]
    assert items[0].source == "AI news · ai_news feed" and items[0].snippet == "blurb 0"
    assert digest_items(TOPICS, store, THURSDAY + timedelta(days=1), "morning_digest") == []


def test_digest_skips_duplicate_urls_and_falls_through_to_next_item(store: Store) -> None:
    ai = _report("ai_news", THURSDAY, 4)
    sec = _report("security", THURSDAY, 3)
    # security's top item is the same story (case/slash/fragment differ) as ai_news' top item.
    sec["items"][0]["url"] = "https://EXAMPLE.com/ai_news-feed/0/#x"
    store.save_report("ai_news", THURSDAY - timedelta(hours=2), ai)
    store.save_report("security", THURSDAY - timedelta(hours=1), sec)
    urls = [i.url for i in digest_items(TOPICS, store, THURSDAY, "morning_digest")]
    assert urls == [
        "https://example.com/ai_news-feed/0",
        "https://example.com/security-feed/1",
        "https://example.com/ai_news-feed/1",
        "https://example.com/security-feed/2",
    ]


def test_digest_caps_items_per_topic(store: Store) -> None:
    store.save_report("ai_news", THURSDAY - timedelta(hours=1), _report("ai_news", THURSDAY, 6))
    assert len(digest_items(TOPICS, store, THURSDAY, "morning_digest")) == 2


def test_weekly_takes_each_reports_top_item_from_last_7_days(store: Store) -> None:
    sunday = datetime(2026, 11, 29, 10, 0, tzinfo=IST)
    for days_ago in (1, 3, 8):
        when = sunday - timedelta(days=days_ago)
        data = _report(f"ai{days_ago}", when, 2, headline=f"Theme {days_ago}")
        store.save_report("ai_news", when, data)
    items = weekly_items(TOPICS, store, sunday, "weekly_review")
    assert [i.url for i in items] == [
        "https://example.com/ai1-feed/0",  # newest first
        "https://example.com/ai3-feed/0",
    ]
    assert items[0].snippet == "Theme 1 — blurb 0"


class Recorder:
    def __init__(self) -> None:
        self.delivered: list[Report] = []

    def deliver(self, report: Report, emoji: str) -> None:
        self.delivered.append(report)


def _deps(settings: Settings, store: Store, clock: Clock, http: FakeHttp | None = None) -> Deps:
    http = http or FakeHttp()
    return Deps(
        http=http,
        clock=clock,
        store=store,
        summarizer=Summarizer(settings, http, clock, log=io.StringIO()),
        deliverer=Recorder(),
        topics=TOPICS,
        settings=settings,
        log=io.StringIO(),
        out=io.StringIO(),
    )


def test_digest_run_ai_and_fallback(store: Store, settings: Settings) -> None:
    store.save_report("ai_news", THURSDAY, _report("ai_news", THURSDAY, 3))
    http = FakeHttp()
    content = (
        '{"headline": "Big morning", "summary": "One.", "blurbs": [{"index": 2, "text": "x"}]}'
    )
    http.post_responses = [{"choices": [{"message": {"content": content}}]}, RuntimeError("down")]
    deps = _deps(settings, store, FixedClock(THURSDAY), http)
    digest = TOPICS[2]
    result = run_topic(digest, deps)
    assert result.status == "delivered" and result.report is not None
    assert result.report.headline == "Big morning"
    assert result.report.items[0].url == "https://example.com/ai_news-feed/1"
    assert "morning digest" in http.posts[0][2]["messages"][0]["content"].lower()
    # AI failure: a plain list of the same stored items.
    result = run_topic(digest, deps)
    assert result.status == "delivered" and result.report is not None
    assert "AI summary unavailable" in result.report.summary
    assert len(result.report.items) == 2  # the top 2 items of each of today's reports
    assert store.last_run("morning_digest", ["delivered"]) == THURSDAY


def test_digest_without_reports_sends_nothing(store: Store, settings: Settings) -> None:
    deps = _deps(settings, store, FixedClock(THURSDAY))
    result = run_topic(TOPICS[2], deps)
    assert result.status == "no_new_items" and deps.deliverer.delivered == []  # type: ignore[attr-defined]


# ------------------------------------------------------------------ health check


def test_stale_after_by_schedule() -> None:
    assert stale_after(Schedule(time="06:30")) == timedelta(hours=26)
    assert stale_after(Schedule(every_minutes=60)) == timedelta(hours=26)
    assert stale_after(Schedule(time="07:30", days=WEEKDAYS[:5])) == timedelta(hours=74)
    assert stale_after(Schedule(time="10:00", days=("sun",))) == timedelta(days=6, hours=26)


def test_health_content_and_stale_detection(store: Store, settings: Settings) -> None:
    now = THURSDAY
    store.record_run("ai_news", now - timedelta(hours=2), "delivered")
    store.record_run("security", now - timedelta(hours=30), "no_new_items")
    store.record_run("security", now - timedelta(hours=1), "failed")  # failures don't count
    earlier = now - timedelta(hours=2)
    store.save_report("ai_news", earlier, _report("ai_news", earlier, 1))
    store.record_source_failure("security", "Feed", now - timedelta(hours=3), "HttpError: 503")
    store.record_source_failure("security", "Feed", now - timedelta(hours=2), "HttpError: 502")
    store.record_source_failure("ai_news", "Old", now - timedelta(hours=30), "too old")
    health = collect_health(TOPICS, store, settings, now)
    report = health_report("health_check", "Health check", health, now)
    text = report.summary
    assert [t.topic.id for t in health.stale] == ["security"]
    assert "⚠️ STALE security" in text and "(30 h ago)" in text
    assert "✅ ai_news: 26 Nov 06:30 IST (2 h ago) · 1 reports" in text
    assert "morning_digest: never run yet" in text
    assert "health_check" not in text  # not listed in its own report
    assert "security / Feed ×2: HttpError: 502" in text and "Old" not in text
    assert "Disk free (data dir):" in text and "Process memory:" in text
    assert "Groq: configured" in text and "Telegram: NOT configured" in text
    assert report.headline.startswith("⚠️ 1 stale: security; 1 failing source(s)")
    assert report.items == ()
    # Both channels keep the lines apart.
    assert "\n⚠️ STALE security" in format_telegram(report, "🩺")
    assert "\n⚠️ STALE security" in format_report(report, "🩺")


def test_health_all_good(store: Store, settings: Settings) -> None:
    for t in TOPICS:
        store.record_run(t.id, THURSDAY - timedelta(hours=1), "delivered")
    health = collect_health(TOPICS, store, settings, THURSDAY)
    assert health_report("h", "Health", health, THURSDAY).headline == "✅ All good"


def test_health_goes_to_owner_only(store: Store, settings: Settings, tmp_path: Path) -> None:
    now = THURSDAY
    store.subscribe(111, ["ai_news", "health_check"], now)  # even a forged subscription
    subs = OutboxSubscribers(tmp_path, store, FixedClock(now), audience_map(TOPICS))
    assert subs.recipients("health_check") == [] and subs.recipients("ai_news") == [111]
    deps = _deps(settings, store, FixedClock(now))
    result = run_topic(TOPICS[4], deps)
    assert result.status == "delivered"
    assert deps.deliverer.delivered[0].topic_id == "health_check"  # type: ignore[attr-defined]


def test_health_cli(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("MYBUDDY_DATA_DIR", str(tmp_path / "data"))
    for name in ("GROQ_API_KEY", "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID", "MYBUDDY_CONFIG"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(cli, "make_clock", lambda: FixedClock(THURSDAY))
    code = cli.main(["--env-file", str(tmp_path / "none"), "health"])
    out = capsys.readouterr().out
    assert code == 0  # nothing has run yet: nothing stale, no failures
    assert out.startswith("Health check\n⚠️ no Groq key")
    assert "policy_watch: never run yet" in out and "Groq: NOT configured" in out
    with Store(tmp_path / "data") as s:
        s.record_source_failure("ai_news", "Feed", THURSDAY, "HTTP 500")
    assert cli.main(["--env-file", str(tmp_path / "none"), "health"]) == 0  # failed source: WARN
    capsys.readouterr()
    with Store(tmp_path / "data") as s:
        s.record_run("ai_news", THURSDAY - timedelta(days=2), "delivered")
    assert cli.main(["--env-file", str(tmp_path / "none"), "health"]) == 1
    assert "⚠️ STALE ai_news" in capsys.readouterr().out


# ------------------------------------------------------- run log and dedupe_with


def _news_deps(
    settings: Settings, store: Store, sources: dict[str, FakeSource], topics: list[Topic]
) -> Deps:
    def build(spec: dict[str, Any], http: HttpClient, c: Clock) -> Source:
        return sources[spec["name"]]

    deps = _deps(replace(settings, fake=True), store, FixedClock(THURSDAY))
    deps.build_source = build
    deps.topics = topics
    return deps


def test_real_runs_record_status_and_source_failures(store: Store, settings: Settings) -> None:
    topic = make_topic(sources=({"type": "fake", "name": "A"}, {"type": "fake", "name": "B"}))
    sources = {"A": FakeSource("A", [make_item(1)]), "B": FakeSource("B", error=OSError("boom"))}
    deps = _news_deps(settings, store, sources, [topic])
    assert run_topic(topic, deps, dry_run=True).status == "dry_run"
    assert store.last_run("ai_news", ["dry_run"]) is None  # dry runs leave no trace
    assert store.source_failures_since(THURSDAY - timedelta(days=1)) == []
    assert run_topic(topic, deps).status == "delivered"
    assert store.last_run("ai_news", ["delivered"]) == THURSDAY
    (failure,) = store.source_failures_since(THURSDAY - timedelta(days=1))
    assert (failure.topic_id, failure.source, failure.error) == ("ai_news", "B", "OSError: boom")


def test_dedupe_with_skips_items_other_topics_sent(store: Store, settings: Settings) -> None:
    other = make_topic(id="startups", bit=1)
    topic = make_topic(id="industry", website=False, bit=None, dedupe_with=("startups",))
    store.mark_seen("startups", [make_item(1).url], THURSDAY)
    sources = {"Feed": FakeSource("Feed", [make_item(1), make_item(2)])}
    deps = _news_deps(settings, store, sources, [other, topic])
    result = run_topic(topic, deps)
    assert result.status == "delivered" and result.new == 1
    assert result.report is not None and [i.url for i in result.report.items] == [make_item(2).url]


# --------------------------------------------------------------------- schedules


def test_new_schedules() -> None:
    weekly = Schedule(time="10:00", days=("sun",))
    thursday = datetime(2026, 11, 26, 11, 0, tzinfo=IST)
    assert next_run(weekly, thursday) == datetime(2026, 11, 29, 10, 0, tzinfo=IST)
    sunday_after = datetime(2026, 11, 29, 10, 0, tzinfo=IST)
    assert next_run(weekly, sunday_after) == datetime(2026, 12, 6, 10, 0, tzinfo=IST)
    assert next_run(Schedule(time="08:30"), thursday) == datetime(2026, 11, 27, 8, 30, tzinfo=IST)


def test_shipped_new_topics() -> None:
    from mybuddy.config import DEFAULT_CONFIG_PATH, load_topics

    by_id = {t.id: t for t in load_topics(DEFAULT_CONFIG_PATH)}
    expected = {
        "morning_digest": ("08:30", "digest", WEEKDAYS),
        "health_check": ("10:15", "health", WEEKDAYS),
        "policy_watch": ("09:15", "news", WEEKDAYS),
        "product_recalls": ("09:30", "news", WEEKDAYS),
        "industry_signals": ("09:45", "news", WEEKDAYS),
        "weekly_review": ("10:00", "weekly", ("sun",)),
    }
    for topic_id, (time, kind, days) in expected.items():
        t = by_id[topic_id]
        assert (t.schedule.time, t.kind, t.schedule.days) == (time, kind, days)
        assert not t.on_website and t.bit is None
    assert by_id["health_check"].audience is None and not by_id["health_check"].ai
    ai_runs_per_day = sum(1 for t in by_id.values() if t.ai and t.schedule.days == WEEKDAYS)
    assert ai_runs_per_day <= 11  # documented Groq budget in topics.yaml
