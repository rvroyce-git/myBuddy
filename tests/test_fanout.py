"""Report delivery to the owner plus public Telegram subscribers."""

from __future__ import annotations

import io
import json
from collections.abc import Iterator
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from conftest import MONDAY, FakeHttp, FakeSource, FixedClock, make_item

from mybuddy.config import DEFAULT_CONFIG_PATH, Settings, Topic, load_topics
from mybuddy.deliver import (
    TELEGRAM_LIMIT,
    DeliveryError,
    FanoutDeliverer,
    FanoutStats,
    OutboxSubscribers,
    OutboxWriter,
    TelegramBot,
    TelegramSubscribers,
    make_deliverer,
)
from mybuddy.engine import Deps, run_topic
from mybuddy.http import HttpError
from mybuddy.models import Clock, HttpClient, Report, ReportItem, Source
from mybuddy.store import Store
from mybuddy.subscriptions import audience_map
from mybuddy.summarize import Summarizer

TOKEN = "123456789:AAH-secret_token_value_1234567890"
SEND = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
OWNER = 111222333
TOPICS: list[Topic] = load_topics(DEFAULT_CONFIG_PATH)
AUDIENCE = audience_map(TOPICS)


class ChatHttp:
    """Fake sendMessage: per-chat queued errors; records (chat_id, text)."""

    def __init__(self) -> None:
        self.sent: list[tuple[Any, str]] = []
        self.errors: dict[Any, list[BaseException]] = {}

    def post_json(self, url: str, payload: Any, headers: dict[str, str] | None = None) -> Any:
        assert url == SEND
        queue = self.errors.get(payload["chat_id"])
        if queue:
            raise queue.pop(0)
        self.sent.append((payload["chat_id"], payload["text"]))
        return {"ok": True}

    def get_json(self, url: str, headers: dict[str, str] | None = None) -> Any:
        raise AssertionError

    def get_bytes(self, url: str, *, accept: str | None = None) -> bytes:
        raise AssertionError

    def chats(self) -> list[Any]:
        return [c for c, _ in self.sent]


def tg_error(status: int, description: str = "", retry_after: int | None = None) -> HttpError:
    body: dict[str, Any] = {"ok": False, "description": description}
    if retry_after is not None:
        body["parameters"] = {"retry_after": retry_after}
    return HttpError(f"HTTP {status} from {SEND}", url=SEND, status=status, body=json.dumps(body))


def report(topic_id: str = "ai_news", n_items: int = 2, blurb: str = "Why.") -> Report:
    items = tuple(
        ReportItem(f"Story {i}", f"https://news.example/{i}", "Example", blurb)
        for i in range(1, n_items + 1)
    )
    return Report(topic_id, "Title", MONDAY, "Head.", "Sum.", items)


@pytest.fixture
def store(data_dir: Path) -> Iterator[Store]:
    s = Store(data_dir)
    s.subscribe(1001, ["ai_news"], MONDAY)
    s.subscribe(1002, ["security"], MONDAY)
    s.subscribe(1003, ["ai_news", "security"], MONDAY)
    s.subscribe(1004, ["startups"], MONDAY)
    s.subscribe(1005, ["ai_news"], MONDAY)
    s.deactivate_subscriber(1005, MONDAY)
    yield s
    s.close()


def subs(
    http: ChatHttp, store: Store, sleeps: list[float] | None = None, **kw: Any
) -> TelegramSubscribers:
    options: dict[str, Any] = {"log": io.StringIO()}
    options.update(kw)
    return TelegramSubscribers(
        TOKEN,
        http,
        store,
        FixedClock(MONDAY),
        AUDIENCE,
        sleep=(sleeps if sleeps is not None else []).append,
        **options,
    )


@pytest.mark.parametrize(
    ("topic_id", "chats"),
    [
        ("ai_news", [1001, 1003]),  # inactive 1005 skipped
        ("security", [1002, 1003]),
        ("kev_alerts", [1002, 1003]),  # hourly KEV alerts go to security subscribers
        ("startups", [1004]),
        ("github_trending", []),
        ("not_a_topic", []),
    ],
)
def test_topic_routing(store: Store, topic_id: str, chats: list[int]) -> None:
    http = ChatHttp()
    stats = subs(http, store).send(report(topic_id), "🧠")
    assert http.chats() == chats
    assert stats == FanoutStats(recipients=len(chats), sent=len(chats))


def test_owner_gets_one_copy_and_goes_first(store: Store, settings: Settings) -> None:
    store.subscribe(OWNER, ["ai_news"], MONDAY)  # owner also subscribed via the website
    http = ChatHttp()
    s = replace(settings, telegram_bot_token=TOKEN, telegram_chat_id=str(OWNER))
    deliverer = make_deliverer(s, http, FixedClock(MONDAY), store=store, topics=TOPICS)
    assert isinstance(deliverer, FanoutDeliverer)
    assert isinstance(deliverer.primary, TelegramBot)
    assert isinstance(deliverer.subscribers, TelegramSubscribers)
    deliverer.subscribers.sleep = lambda s: None
    deliverer.deliver(report(), "🧠")
    assert http.chats() == [str(OWNER), 1001, 1003]
    assert http.sent[0][1] == http.sent[1][1]  # same message for everyone


def test_blocked_and_unknown_chats_are_deactivated(store: Store) -> None:
    http = ChatHttp()
    http.errors[1001] = [tg_error(403, "Forbidden: bot was blocked by the user")]
    http.errors[1003] = [tg_error(400, "Bad Request: chat not found")]
    log = io.StringIO()
    stats = subs(http, store, log=log).send(report(), "🧠")
    assert stats == FanoutStats(recipients=2, sent=0, failed=0, deactivated=2)
    assert store.active_subscribers("ai_news") == []
    assert store.active_subscribers("security") == [1002]
    assert "…001" in log.getvalue() and "1001" not in log.getvalue()


def test_user_deactivated_403_and_other_400(store: Store) -> None:
    http = ChatHttp()
    http.errors[1002] = [tg_error(403, "Forbidden: user is deactivated")]
    http.errors[1003] = [tg_error(400, "Bad Request: message is too long")]
    stats = subs(http, store).send(report("security"), "🛡️")
    assert stats == FanoutStats(recipients=2, sent=0, failed=1, deactivated=1)
    assert store.get_subscriber(1003).active  # type: ignore[union-attr]  # not a "gone" error
    assert not store.get_subscriber(1002).active  # type: ignore[union-attr]


def test_429_honours_retry_after(store: Store) -> None:
    http = ChatHttp()
    http.errors[1001] = [tg_error(429, "Too Many Requests", retry_after=4)]
    sleeps: list[float] = []
    stats = subs(http, store, sleeps, interval=0.04).send(report(), "🧠")
    assert stats.sent == 2 and http.chats() == [1001, 1003]
    assert sleeps == [4.0, 0.04]


def test_429_retry_after_is_capped(store: Store) -> None:
    http = ChatHttp()
    http.errors[1003] = [tg_error(429, retry_after=600)] * 5
    sleeps: list[float] = []
    stats = subs(http, store, sleeps, interval=0).send(report(), "🧠")
    assert stats == FanoutStats(recipients=2, sent=1, failed=1)
    assert max(sleeps) == 30.0


def test_throttle_and_multipart_order(store: Store) -> None:
    http = ChatHttp()
    sleeps: list[float] = []
    big = report(n_items=60, blurb="word " * 30)
    stats = subs(http, store, sleeps, interval=0.04, part_delay=1.0).send(big, "🧠")
    n_parts = len(http.sent) // 2
    assert n_parts > 1 and stats.sent == 2
    assert all(len(text) <= TELEGRAM_LIMIT for _, text in http.sent)
    # Part 1 to everyone, then part 2 ...: each chat's parts stay in order.
    assert http.chats() == [1001, 1003] * n_parts
    texts_1001 = [t for c, t in http.sent if c == 1001]
    assert texts_1001 == [t for c, t in http.sent if c == 1003]
    assert sleeps.count(1.0) == n_parts - 1 and sleeps.count(0.04) == len(http.sent) - 1


def test_failed_chat_skips_later_parts(store: Store) -> None:
    http = ChatHttp()
    http.errors[1001] = [tg_error(500, "Internal")]
    stats = subs(http, store).send(report(n_items=60, blurb="word " * 30), "🧠")
    assert 1001 not in http.chats() and stats == FanoutStats(recipients=2, sent=1, failed=1)


def test_unexpected_exception_is_contained(store: Store) -> None:
    http = ChatHttp()
    http.errors[1001] = [RuntimeError("weird")]
    stats = subs(http, store).send(report(), "🧠")
    assert stats == FanoutStats(recipients=2, sent=1, failed=1)


class Primary:
    def __init__(self, fail: bool = False) -> None:
        self.fail = fail
        self.calls = 0

    def deliver(self, report: Report, emoji: str) -> None:
        self.calls += 1
        if self.fail:
            raise DeliveryError("owner channel down")


class Boom:
    def send(self, report: Report, emoji: str) -> FanoutStats:
        raise RuntimeError("store locked")


def test_subscriber_crash_never_fails_delivery() -> None:
    log = io.StringIO()
    fan = FanoutDeliverer(Primary(), Boom(), log=log)
    fan.deliver(report(), "🧠")
    assert "subscriber delivery crashed: RuntimeError" in log.getvalue()


def test_owner_failure_fails_and_skips_subscribers(store: Store) -> None:
    http = ChatHttp()
    fan = FanoutDeliverer(Primary(fail=True), subs(http, store))
    with pytest.raises(DeliveryError):
        fan.deliver(report(), "🧠")
    assert http.sent == []  # they get it when the retried run succeeds: no double sends


def _deps(settings: Settings, store: Store, deliverer: Any) -> Deps:
    def build(spec: dict[str, Any], http: HttpClient, c: Clock) -> Source:
        return FakeSource(spec["name"], [make_item(1, spec["name"])])

    clock = FixedClock(MONDAY)
    return Deps(
        http=FakeHttp(),
        clock=clock,
        store=store,
        summarizer=Summarizer(replace(settings, fake=True), FakeHttp(), clock),
        deliverer=deliverer,
        build_source=build,
        log=io.StringIO(),
    )


def test_seen_marking_follows_owner_not_subscribers(store: Store, settings: Settings) -> None:
    topic = next(t for t in TOPICS if t.id == "ai_news")
    http = ChatHttp()
    http.errors = {1001: [tg_error(500)], 1003: [tg_error(403, "Forbidden: blocked")]}
    deps = _deps(settings, store, FanoutDeliverer(Primary(), subs(http, store)))
    result = run_topic(topic, deps)
    assert result.status == "delivered"  # subscriber failures don't fail the run
    assert store.unseen("ai_news", [make_item(1, topic.sources[0]["name"]).url]) == set()

    kev = next(t for t in TOPICS if t.id == "kev_alerts")
    deps.deliverer = FanoutDeliverer(Primary(fail=True), subs(ChatHttp(), store))
    failed = run_topic(kev, deps)
    assert failed.status == "failed"  # owner failure still fails it: items stay unseen
    assert store.unseen("kev_alerts", [make_item(1, "CISA KEV").url]) != set()


def test_fake_mode_writes_labelled_outbox(store: Store, settings: Settings) -> None:
    http = FakeHttp()
    s = replace(settings, fake=True, telegram_bot_token=TOKEN, telegram_chat_id=str(OWNER))
    deliverer = make_deliverer(s, http, FixedClock(MONDAY), store=store, topics=TOPICS)
    assert isinstance(deliverer, FanoutDeliverer)
    assert isinstance(deliverer.primary, OutboxWriter)
    assert isinstance(deliverer.subscribers, OutboxSubscribers)
    deliverer.deliver(report("kev_alerts"), "🚨")
    files = sorted(p.name for p in (settings.data_dir / "outbox").iterdir())
    assert files == ["kev_alerts-20261123-063000-subscribers.md", "kev_alerts-20261123-063000.md"]
    text = (settings.data_dir / "outbox" / files[0]).read_text(encoding="utf-8")
    assert text.startswith("<!-- FAKE SUBSCRIBER DELIVERY: 2 Telegram subscriber(s) (…002, …003)")
    assert "1002" not in text and "<b>Head.</b>" in text
    assert http.calls == []
    deliverer.deliver(report("github_trending"), "⭐")  # nobody subscribed: no extra file
    assert len(list((settings.data_dir / "outbox").iterdir())) == 3


def test_make_deliverer_without_subscriber_channel(store: Store, settings: Settings) -> None:
    clock = FixedClock(MONDAY)
    plain = make_deliverer(settings, FakeHttp(), clock, store=store, topics=TOPICS)
    assert isinstance(plain, OutboxWriter)  # no token, not fake: owner channel only
    hook = replace(settings, discord_webhook_url="https://discord.example/hook")
    no_store = make_deliverer(replace(hook, telegram_bot_token=TOKEN), FakeHttp(), clock)
    assert not isinstance(no_store, FanoutDeliverer)
    discord_plus_subs = make_deliverer(
        replace(hook, telegram_bot_token=TOKEN), FakeHttp(), clock, store=store, topics=TOPICS
    )
    assert isinstance(discord_plus_subs, FanoutDeliverer)
    assert discord_plus_subs.subscribers.exclude is None  # type: ignore[attr-defined]


class Clockwork:
    """Fake monotonic clock that advances only when the code under test sleeps."""

    def __init__(self) -> None:
        self.t = 0.0
        self.sleeps: list[float] = []

    def __call__(self) -> float:
        return self.t

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.t += seconds


def _many(store: Store, n: int) -> list[int]:
    chats = [2000 + i for i in range(n)]
    for chat in chats:
        store.subscribe(chat, ["research_papers"], MONDAY)
    return chats


def test_fanout_stops_at_time_budget(store: Store) -> None:
    chats = _many(store, 10)
    http, clock, log = ChatHttp(), Clockwork(), io.StringIO()
    sender = subs(http, store, interval=10.0, deadline=25.0, monotonic=clock, log=log)
    sender.sleep = clock.sleep
    stats = sender.send(report("research_papers"), "📄")
    assert http.chats() == chats[:3]  # sent at t=0, 10, 20; t=30 is past the budget
    assert stats == FanoutStats(recipients=10, sent=3, skipped=7)
    assert clock.t <= 30 and "(time budget of 25s used up); 7 skipped" in log.getvalue()


def test_fanout_time_budget_counts_only_complete_reports(store: Store) -> None:
    _many(store, 4)
    http, clock = ChatHttp(), Clockwork()
    sender = subs(http, store, interval=1.0, part_delay=5.0, deadline=6.0, monotonic=clock)
    sender.sleep = clock.sleep
    big = report("research_papers", n_items=60, blurb="word " * 30)
    stats = sender.send(big, "📄")
    assert stats.sent == 0 and stats.skipped == 4  # nobody got every part
    assert len(http.sent) == 4  # first part reached everyone before time ran out


def test_fanout_stops_after_consecutive_network_errors(store: Store) -> None:
    chats = _many(store, 9)
    http, log = ChatHttp(), io.StringIO()
    for chat in chats:
        http.errors[chat] = [HttpError("down", status=None)]
    http.errors[chats[2]] = []  # one success resets the streak
    stats = subs(http, store, log=log).send(report("research_papers"), "📄")
    assert http.chats() == [chats[2]]
    assert stats == FanoutStats(recipients=9, sent=1, failed=7, skipped=1)
    assert "5 network errors in a row" in log.getvalue()


def test_fanout_server_errors_count_as_network(store: Store) -> None:
    chats = _many(store, 7)
    http = ChatHttp()
    for chat in chats:
        http.errors[chat] = [tg_error(502, "Bad Gateway")]
    stats = subs(http, store).send(report("research_papers"), "📄")
    assert stats == FanoutStats(recipients=7, sent=0, failed=5, skipped=2)


def test_fanout_summary_logged(store: Store) -> None:
    log = io.StringIO()
    http = ChatHttp()
    http.errors[1001] = [tg_error(403, "Forbidden: bot was blocked by the user")]
    FanoutDeliverer(Primary(), subs(http, store), log=log).deliver(report(), "🧠")
    assert "ai_news: subscribers: 1 sent, 0 failed, 1 deactivated, 0 skipped" in log.getvalue()
