"""Chat guards: owner-only context, quiet window, bare-domain stripping, limits, SITE_URL."""

from __future__ import annotations

import io
from collections.abc import Iterator
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest
from conftest import MONDAY, FakeHttp, FixedClock, make_topic
from test_bot import OWNER, TOKEN, TOPICS, TgHttp, Ticker, msg
from test_bot_access import CHAT_TIME, FRIEND, STRANGER, ai, chat_settings, seed_reports

from mybuddy import chat as chatmod
from mybuddy.bot import PRIVATE, SubscriptionBot
from mybuddy.chat import ChatResponder, Context, clean_answer, in_quiet_window
from mybuddy.config import (
    DEFAULT_SITE_URL,
    MAX_CHAT_DAILY_TOKEN_BUDGET,
    Schedule,
    Settings,
)
from mybuddy.configedit import LIMITS
from mybuddy.store import Store

HEALTH_TEXT = "Owner only: 3 sources failing"


@pytest.fixture
def store(data_dir: Path) -> Iterator[Store]:
    s = Store(data_dir)
    yield s
    s.close()


def seed_health(store: Store) -> None:
    store.save_report(
        "health_check",
        MONDAY,
        {
            "headline": HEALTH_TEXT,
            "items": [{"title": "Disk 91% full", "url": "https://health.example/disk"}],
        },
    )


def prompt_of(aihttp: FakeHttp) -> str:
    [(_, _, payload, _)] = aihttp.posts
    return "\n".join(m["content"] for m in payload["messages"])


# --------------------------------------------------------------------------- M1 owner-only


def test_health_report_is_context_for_the_owner_only(store: Store) -> None:
    seed_reports(store)
    seed_health(store)
    public = chatmod.build_context(store, TOPICS)
    assert HEALTH_TEXT not in public.text and "health_check" not in public.text
    assert not any(label.startswith("health_check") for label in public.links)
    owner = chatmod.build_context(store, TOPICS, owner=True)
    assert HEALTH_TEXT in owner.text and "health_check #1" in owner.links


@pytest.mark.parametrize(("chat_id", "sees"), [(OWNER, True), (FRIEND, False)])
def test_allowlisted_chat_never_gets_health_context(store: Store, chat_id: int, sees: bool) -> None:
    seed_reports(store)
    seed_health(store)
    aihttp = FakeHttp()
    aihttp.post_responses = [ai("Fine.")]
    responder = ChatResponder(chat_settings(), aihttp, store, TOPICS, FixedClock(CHAT_TIME))
    responder.answer(chat_id, "how is the server doing?")
    assert (HEALTH_TEXT in prompt_of(aihttp)) is sees


def test_no_owner_configured_means_no_health_context(store: Store) -> None:
    seed_reports(store)
    seed_health(store)
    aihttp = FakeHttp()
    aihttp.post_responses = [ai("Fine.")]
    settings = chat_settings(telegram_chat_id=None)
    ChatResponder(settings, aihttp, store, TOPICS, FixedClock(CHAT_TIME)).answer(OWNER, "q")
    assert HEALTH_TEXT not in prompt_of(aihttp)


# --------------------------------------------------------------------------- M2 limits


def test_chat_defaults_and_token_budget_ceiling() -> None:
    s = Settings.from_env({})
    assert s.chat_rate_per_min == 2 and MAX_CHAT_DAILY_TOKEN_BUDGET == 80_000
    budget = Settings.from_env({"CHAT_DAILY_TOKEN_BUDGET": "80000"}).chat_daily_token_budget
    assert budget == 80_000
    for too_much in ("80001", "150000", "10000000"):
        s = Settings.from_env({"CHAT_DAILY_TOKEN_BUDGET": too_much})
        assert s.chat_daily_token_budget == 60_000  # the default, never above the ceiling
    assert LIMITS["chat-tokens"][2] == 80_000


def test_quiet_window_around_ai_runs() -> None:
    topics = [make_topic(schedule=Schedule(time="06:30"))]
    run = MONDAY  # 06:30 IST
    for minutes in (-2, -1, 0, 1, 2):
        assert in_quiet_window(topics, run + timedelta(minutes=minutes)), minutes
    for minutes in (-3, 3, 10, -60):
        assert not in_quiet_window(topics, run + timedelta(minutes=minutes)), minutes
    assert not in_quiet_window([make_topic(enabled=False)], run)
    no_ai = make_topic(ai=False, prompt=None, schedule=Schedule(every_minutes=60))
    assert not in_quiet_window([no_ai], run)  # only AI topics use Groq
    # Around midnight the window spans two days.
    late = [make_topic(schedule=Schedule(time="00:00"))]
    assert in_quiet_window(late, MONDAY.replace(hour=23, minute=59))
    assert in_quiet_window(late, MONDAY.replace(hour=0, minute=1))


def test_quiet_window_reply_makes_no_ai_call_and_is_not_counted(store: Store) -> None:
    seed_reports(store)
    aihttp = FakeHttp()
    log = io.StringIO()
    clock = FixedClock(MONDAY + timedelta(minutes=1))  # 06:31, ai_news runs at 06:30
    responder = ChatResponder(chat_settings(), aihttp, store, TOPICS, clock, log=log)
    assert responder.answer(OWNER, "what's new?") == chatmod.QUIET
    assert aihttp.posts == []
    assert store.chat_uses_on(OWNER, chatmod.ist_day(clock)) == 0
    assert "quiet window" in log.getvalue()
    clock.advance(minutes=9)  # 06:40: free again
    aihttp.post_responses = [ai("Model X [ai_news #1].")]
    assert "Model X" in responder.answer(OWNER, "what's new?")


# --------------------------------------------------------------------------- L1 bare domains

CTX = Context(text="", links={"ai_news #1": "https://example.com/model-x"})


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Log in at evil.com/login now.", "Log in at [link removed] now."),
        ("Try evil.co", "Try [link removed]"),
        ("See sub.evil.com?q=1 or EVIL.COM:8080/x", "See [link removed] or [link removed]"),
        ("Go to ftp://bad.example/x", "Go to ftp://[link removed]"),
        ("Python 3.4.2 fixes it", None),
        ("Edit config.yaml and app.py; Node.js 22", None),
        ("Mail press@example.org or john.doe@evil.com", None),
        ("e.g. the U.S. and EU, i.e. $1.5bn at 10.30am", None),
        ("More at example.com/other and www.example.com", None),  # a stored report's host
        ("Cited [ai_news #1].", None),
    ],
)
def test_bare_domains_removed_but_words_kept(raw: str, expected: str | None) -> None:
    out = clean_answer(raw, CTX).split("\n\n")[0]
    assert out == (raw if expected is None else expected)


def test_full_urls_still_need_an_exact_stored_match() -> None:
    out = clean_answer("https://example.com/model-x and https://example.com/x", CTX)
    assert out.startswith("https://example.com/model-x and [link removed]")


# --------------------------------------------------------------------------- L9 SITE_URL


def test_site_url_setting() -> None:
    assert Settings().site_url == DEFAULT_SITE_URL == "https://rvroyce-git.github.io/myBuddy/"
    custom = "https://news.example.org/buddy/"
    assert Settings.from_env({"SITE_URL": custom}).site_url == custom
    for bad in ("http://news.example.org/", "javascript:alert(1)", "https://x.org/?a=b c"):
        assert Settings.from_env({"SITE_URL": bad}).site_url == DEFAULT_SITE_URL


def test_private_reply_links_the_configured_site(store: Store) -> None:
    def bot(**kw: Any) -> tuple[SubscriptionBot, TgHttp]:
        tg = TgHttp()
        b = SubscriptionBot(
            TOKEN,
            tg,
            store,
            TOPICS,
            FixedClock(CHAT_TIME),
            owner_chat_id=str(OWNER),
            monotonic=Ticker(),
            log=io.StringIO(),
            **kw,
        )
        return b, tg

    b, tg = bot()
    b.handle_update(msg("/start", chat=STRANGER))
    assert tg.texts() == [PRIVATE] and DEFAULT_SITE_URL in PRIVATE
    b, tg = bot(site_url="https://news.example.org/buddy/")
    b.handle_update(msg("/start", chat=STRANGER + 1))
    [reply] = tg.texts()
    assert reply.endswith("https://news.example.org/buddy/") and "github.io" not in reply


def test_check_warns_about_a_token_budget_above_the_maximum(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from mybuddy.cli.check import access_lines

    monkeypatch.setenv("CHAT_DAILY_TOKEN_BUDGET", "150000")
    lines = access_lines(Settings.from_env({"CHAT_DAILY_TOKEN_BUDGET": "150000"}))
    assert any(kind == "warning" and "80000 maximum" in text for kind, text in lines)
    monkeypatch.setenv("CHAT_DAILY_TOKEN_BUDGET", "80000")
    lines = access_lines(Settings.from_env({"CHAT_DAILY_TOKEN_BUDGET": "80000"}))
    assert not any("maximum" in text for _, text in lines)
