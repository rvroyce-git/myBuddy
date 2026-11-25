"""Telegram subscription bot: commands, picker callbacks, limits, flood guard and the loop."""

from __future__ import annotations

import io
import os
import signal
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from conftest import MONDAY, FixedClock

from mybuddy.bot import (
    CALLBACK_FLOOD_LIMIT,
    CALLBACK_MAX_BYTES,
    EXPIRED,
    FULL,
    HELP,
    NOT_SUBSCRIBED,
    OWNER_NOTE,
    PICK_ONE,
    SAVED,
    SLOW_DOWN,
    STOPPED,
    UNKNOWN,
    WELCOME,
    SubscriptionBot,
    parse_command,
)
from mybuddy.config import DEFAULT_CONFIG_PATH, Topic, load_topics
from mybuddy.http import HttpError
from mybuddy.store import Store

TOKEN = "123456789:AAH-secret_token_value_1234567890"
API = f"https://api.telegram.org/bot{TOKEN}/"
CHAT = 555666777
OWNER = 111222333
TOPICS: list[Topic] = load_topics(DEFAULT_CONFIG_PATH)


class TgHttp:
    """Fake Bot API: replies per method from queues, records (method, payload)."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.queue: dict[str, list[Any]] = {}

    def post_json(self, url: str, payload: Any, headers: dict[str, str] | None = None) -> Any:
        assert url.startswith(API), url
        method = url[len(API) :]
        self.calls.append((method, payload))
        queue = self.queue.get(method)
        reply = queue.pop(0) if queue else {"ok": True, "result": True}
        if isinstance(reply, BaseException):
            raise reply
        return reply

    def get_json(self, url: str, headers: dict[str, str] | None = None) -> Any:
        raise AssertionError("bot must not GET")

    def get_bytes(self, url: str, *, accept: str | None = None) -> bytes:
        raise AssertionError("bot must not GET")

    def of(self, method: str) -> list[dict[str, Any]]:
        return [p for m, p in self.calls if m == method]

    def texts(self) -> list[str]:
        return [p["text"] for p in self.of("sendMessage")]


class Ticker:
    def __init__(self) -> None:
        self.t = 1000.0

    def __call__(self) -> float:
        return self.t


@pytest.fixture
def store(data_dir: Path) -> Iterator[Store]:
    s = Store(data_dir)
    yield s
    s.close()


@pytest.fixture
def tg() -> TgHttp:
    return TgHttp()


@pytest.fixture
def ticker() -> Ticker:
    return Ticker()


@pytest.fixture
def log() -> io.StringIO:
    return io.StringIO()


@pytest.fixture
def bot(tg: TgHttp, store: Store, ticker: Ticker, log: io.StringIO) -> SubscriptionBot:
    return make_bot(tg, store, ticker, log)


def make_bot(
    tg: TgHttp, store: Store, ticker: Ticker, log: io.StringIO, **kw: Any
) -> SubscriptionBot:
    # These tests cover the subscription flow itself, so they run with PUBLIC_SIGNUP on;
    # private mode (the default) is covered in test_bot_access.py.
    options: dict[str, Any] = {
        "owner_chat_id": str(OWNER),
        "monotonic": ticker,
        "log": log,
        "public_signup": True,
    }
    options.update(kw)
    return SubscriptionBot(TOKEN, tg, store, TOPICS, FixedClock(MONDAY), **options)


def msg(text: Any, chat: int = CHAT, kind: str = "private", uid: int = 1) -> dict[str, Any]:
    message: dict[str, Any] = {
        "message_id": 10,
        "chat": {"id": chat, "type": kind},
        "from": {"id": chat, "is_bot": False, "first_name": "Visitor"},
    }
    if text is not None:
        message["text"] = text
    return {"update_id": uid, "message": message}


def cb(data: Any, chat: int = CHAT, kind: str = "private", uid: int = 1) -> dict[str, Any]:
    return {
        "update_id": uid,
        "callback_query": {
            "id": f"cb{uid}",
            "from": {"id": chat, "is_bot": False},
            "message": {
                "message_id": 77,
                "chat": {"id": chat, "type": kind},
                "from": {"id": 1, "is_bot": True},  # the bot's own picker message
            },
            "data": data,
        },
    }


def marks(markup: dict[str, Any]) -> list[str]:
    """First character of each topic button: ✅ or ⬜."""
    return [row[0]["text"][0] for row in markup["inline_keyboard"][:-1]]


# --------------------------------------------------------------------------- commands


def test_start_with_payload_subscribes(bot: SubscriptionBot, tg: TgHttp, store: Store) -> None:
    bot.handle_update(msg("/start t3"))
    sub = store.get_subscriber(CHAT)
    assert sub is not None and sub.active and sub.topics == ("ai_news", "security")
    [text] = tg.texts()
    assert "🧠 AI news" in text and "🛡️ Cybersecurity alerts" in text
    assert "Actively exploited vulnerabilities" in text  # kev alerts ride along with security
    assert "Trending GitHub" not in text
    assert text.endswith("/topics to change, /stop to unsubscribe.")
    assert tg.of("sendMessage")[0]["chat_id"] == CHAT
    assert "parse_mode" not in tg.of("sendMessage")[0]  # plain text: titles contain '&'


def test_start_all_topics_and_deep_link_form(bot: SubscriptionBot, store: Store) -> None:
    bot.handle_update(msg("/start@myBuddy_update_bot t7f"))
    sub = store.get_subscriber(CHAT)
    assert sub is not None and len(sub.topics) == 7


@pytest.mark.parametrize("text", ["/start", "/start t0", "/start t80", "/start T3", "/start hi"])
def test_start_without_valid_payload_shows_picker(
    bot: SubscriptionBot, tg: TgHttp, store: Store, text: str
) -> None:
    bot.handle_update(msg(text))
    [sent] = tg.of("sendMessage")
    assert sent["text"] == WELCOME
    markup = sent["reply_markup"]
    assert len(markup["inline_keyboard"]) == 8
    assert marks(markup) == ["⬜"] * 7
    assert markup["inline_keyboard"][0][0]["text"] == "⬜ 🧠 AI news"
    assert markup["inline_keyboard"][-1][0] == {"text": "💾 Save", "callback_data": "save:0"}
    assert store.get_subscriber(CHAT) is None


def test_topics_toggle_and_save(bot: SubscriptionBot, tg: TgHttp, store: Store) -> None:
    bot.handle_update(msg("/start t3"))
    bot.handle_update(msg("/topics"))
    picker = tg.of("sendMessage")[-1]["reply_markup"]
    assert marks(picker) == ["✅", "✅"] + ["⬜"] * 5
    assert picker["inline_keyboard"][1][0]["callback_data"] == "tog:3:security"

    bot.handle_update(cb("tog:3:security", uid=2))  # switch security off
    [edit] = tg.of("editMessageReplyMarkup")
    assert edit["chat_id"] == CHAT and edit["message_id"] == 77
    assert marks(edit["reply_markup"]) == ["✅"] + ["⬜"] * 6
    assert tg.of("answerCallbackQuery")[-1] == {"callback_query_id": "cb2"}
    assert store.get_subscriber(CHAT).topics == ("ai_news", "security")  # type: ignore[union-attr]

    bot.handle_update(cb("tog:1:ai_models", uid=3))  # and AI models on
    assert marks(tg.of("editMessageReplyMarkup")[-1]["reply_markup"])[-1] == "✅"

    bot.handle_update(cb("save:41", uid=4))
    assert store.get_subscriber(CHAT).topics == ("ai_models", "ai_news")  # type: ignore[union-attr]
    removal = tg.of("editMessageReplyMarkup")[-1]
    assert "reply_markup" not in removal  # keyboard removed after saving
    assert tg.of("answerCallbackQuery")[-1] == {"callback_query_id": "cb4", "text": SAVED}
    assert "🤖 New open-source AI models" in tg.texts()[-1]
    assert "Cybersecurity" not in tg.texts()[-1]


def test_save_with_nothing_selected(bot: SubscriptionBot, tg: TgHttp, store: Store) -> None:
    bot.handle_update(cb("save:0"))
    assert tg.of("answerCallbackQuery") == [{"callback_query_id": "cb1", "text": PICK_ONE}]
    assert store.get_subscriber(CHAT) is None and tg.of("sendMessage") == []


def test_stop_and_stop_again(bot: SubscriptionBot, tg: TgHttp, store: Store) -> None:
    bot.handle_update(msg("/start t1"))
    bot.handle_update(msg("/stop"))
    sub = store.get_subscriber(CHAT)
    assert sub is not None and not sub.active
    bot.handle_update(msg("/stop"))
    assert tg.texts()[1:] == [STOPPED, NOT_SUBSCRIBED]
    bot.handle_update(msg("/start t1"))  # comes back
    assert store.active_subscribers("ai_news") == [CHAT]


def test_help_unknown_text_and_non_text(bot: SubscriptionBot, tg: TgHttp) -> None:
    bot.handle_update(msg("/help"))
    bot.handle_update(msg("hello there"))
    bot.handle_update(msg("/frobnicate"))
    bot.handle_update(msg(None))  # sticker / photo
    assert tg.texts() == [HELP, UNKNOWN, UNKNOWN, UNKNOWN]


def test_groups_channels_and_bots_ignored(bot: SubscriptionBot, tg: TgHttp, store: Store) -> None:
    bot.handle_update(msg("/start t3", chat=-100123, kind="group"))
    bot.handle_update(msg("/start t3", chat=-100124, kind="supergroup"))
    bot.handle_update(msg("/start t3", chat=-100125, kind="channel"))
    bot.handle_update({"update_id": 9, "channel_post": {"text": "/start t3"}})
    from_bot = msg("/start t3")
    from_bot["message"]["from"]["is_bot"] = True
    bot.handle_update(from_bot)
    assert tg.calls == []
    bot.handle_update(cb("save:3", chat=-100123, kind="group"))
    # A button press is still answered (silently) so the client spinner never hangs.
    assert tg.calls == [("answerCallbackQuery", {"callback_query_id": "cb1"})]
    assert store.active_subscriber_count() == 0


def test_owner_can_use_commands(bot: SubscriptionBot, tg: TgHttp, store: Store) -> None:
    bot.handle_update(msg("/start t1", chat=OWNER))
    assert OWNER_NOTE in tg.texts()[-1]
    bot.handle_update(msg("/stop", chat=OWNER))
    assert tg.texts()[-1] == f"{STOPPED}\n{OWNER_NOTE}"


def test_subscriber_limit(tg: TgHttp, store: Store, ticker: Ticker, log: io.StringIO) -> None:
    bot = make_bot(tg, store, ticker, log, max_subscribers=1)
    bot.handle_update(msg("/start t1", chat=1001))
    bot.handle_update(msg("/start t1", chat=1002))
    assert tg.texts()[-1] == FULL and store.get_subscriber(1002) is None
    bot.handle_update(cb("save:1", chat=1003))
    assert tg.texts()[-1] == FULL and store.get_subscriber(1003) is None
    assert tg.of("answerCallbackQuery")[-1] == {"callback_query_id": "cb1"}
    # Existing subscribers can still change topics; the owner is always allowed.
    bot.handle_update(msg("/start t3", chat=1001))
    assert store.get_subscriber(1001).topics == ("ai_news", "security")  # type: ignore[union-attr]
    bot.handle_update(msg("/start t1", chat=OWNER))
    assert store.active_subscribers("ai_news") == [1001, OWNER]
    assert "limit reached" in log.getvalue()


def test_flood_guard(bot: SubscriptionBot, tg: TgHttp, ticker: Ticker) -> None:
    for _ in range(12):
        bot.handle_update(msg("/help"))
    assert len(tg.texts()) == 10
    bot.handle_update(msg("/help", chat=4242))  # other chats unaffected
    assert len(tg.texts()) == 11
    ticker.t += 61
    bot.handle_update(msg("/help"))
    assert len(tg.texts()) == 12


def test_picker_taps_have_their_own_budget(bot: SubscriptionBot, tg: TgHttp, store: Store) -> None:
    for _ in range(10):
        bot.handle_update(msg("/help"))  # message budget used up
    bot.handle_update(msg("/topics"))  # ignored
    for i in range(9):  # 9 toggles + Save still work
        bot.handle_update(cb(f"tog:{0 if i % 2 == 0 else 1:x}:ai_news", uid=i))
    bot.handle_update(cb("save:1", uid=99))
    assert len(tg.of("editMessageReplyMarkup")) == 10  # 9 toggles + keyboard removal
    assert tg.of("answerCallbackQuery")[-1] == {"callback_query_id": "cb99", "text": SAVED}
    assert store.active_subscribers("ai_news") == [CHAT]


def test_tap_flood_is_answered_not_ignored(bot: SubscriptionBot, tg: TgHttp) -> None:
    for i in range(CALLBACK_FLOOD_LIMIT + 3):
        bot.handle_update(cb("tog:0:ai_news", uid=i))
    answers = tg.of("answerCallbackQuery")
    assert len(answers) == CALLBACK_FLOOD_LIMIT + 3  # every tap gets an answer
    assert [a.get("text") for a in answers[-3:]] == [SLOW_DOWN] * 3
    assert len(tg.of("editMessageReplyMarkup")) == CALLBACK_FLOOD_LIMIT


@pytest.mark.parametrize(
    "data",
    [
        "tog:3:unknown_topic",
        "tog:3:kev_alerts",  # not a website topic
        "tog:ff:ai_news",  # unknown bits
        "tog:3:AI_NEWS",
        "tog:3:",
        "save:80",
        "save:G",
        "save:",
        "save:1 ",
        "x" * (CALLBACK_MAX_BYTES + 1),
        "tog:" + "0" * 15 + ":" + "a" * 46,
        "drop table",
        "",
        42,
        None,
    ],
)
def test_malformed_callback_data(bot: SubscriptionBot, tg: TgHttp, store: Store, data: Any) -> None:
    bot.handle_update(cb(data))
    assert tg.of("answerCallbackQuery") == [{"callback_query_id": "cb1", "text": EXPIRED}]
    assert tg.of("editMessageReplyMarkup") == [] and tg.of("sendMessage") == []
    assert store.get_subscriber(CHAT) is None


def test_callback_data_fits_telegram_limit(bot: SubscriptionBot) -> None:
    for mask in (0, 0x7F):
        for row in bot.picker_markup(mask)["inline_keyboard"]:
            assert len(row[0]["callback_data"].encode()) <= CALLBACK_MAX_BYTES


def test_parse_command() -> None:
    assert parse_command("/start t3") == ("start", "t3")
    assert parse_command("  /START@myBuddy_update_bot   t3 ") == ("start", "t3")
    assert parse_command("/start@some_other_bot t3") == (None, "")
    assert parse_command("hello /start") == (None, "")
    assert parse_command("") == (None, "")


def test_reply_errors_never_crash_and_403_deactivates(
    bot: SubscriptionBot, tg: TgHttp, store: Store, log: io.StringIO
) -> None:
    store.subscribe(CHAT, ["ai_news"], MONDAY)
    blocked = '{"ok": false, "description": "Forbidden: bot was blocked by the user"}'
    tg.queue["sendMessage"] = [HttpError("403", status=403, body=blocked)]
    bot.handle_update(msg("/help"))
    assert store.active_subscribers("ai_news") == []
    tg.queue["sendMessage"] = [HttpError(f"down {API}", status=None)]
    bot.handle_update(msg("/help"))  # network error on reply: logged, not raised
    assert TOKEN not in log.getvalue() and "secret" not in log.getvalue()


def test_logs_mask_chat_ids_and_hide_text(
    bot: SubscriptionBot, log: io.StringIO, tg: TgHttp
) -> None:
    tg.queue["sendMessage"] = [HttpError("x", status=500)]
    bot.handle_update(msg("/start t3"))
    bot.handle_update(msg("my secret message"))
    for _ in range(10):
        bot.handle_update(msg("/stop"))  # trip the flood guard too
    out = log.getvalue()
    assert "…777" in out and str(CHAT) not in out
    assert "secret message" not in out and "t3" not in out


# --------------------------------------------------------------------------- loop


def updates(*items: dict[str, Any]) -> dict[str, Any]:
    return {"ok": True, "result": list(items)}


def test_poll_persists_offset(
    bot: SubscriptionBot, tg: TgHttp, store: Store, ticker: Ticker, log: io.StringIO
) -> None:
    tg.queue["getUpdates"] = [updates(msg("/start t1", uid=5), msg("/help", uid=6))]
    assert bot.poll_once() == 2
    [poll] = tg.of("getUpdates")
    assert poll == {
        "offset": 0,
        "timeout": 25,
        "allowed_updates": ["message", "callback_query"],
    }
    assert store.load_bot_offset() == 7
    again = make_bot(tg, store, ticker, log)  # restart: no replay
    tg.queue["getUpdates"] = [updates()]
    assert again.poll_once() == 0
    assert tg.of("getUpdates")[-1]["offset"] == 7
    assert store.load_bot_offset() == 7


def test_bad_update_does_not_stop_batch(
    bot: SubscriptionBot, tg: TgHttp, store: Store, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[int] = []
    real = bot.handle_update

    def flaky(update: dict[str, Any]) -> None:
        calls.append(update["update_id"])
        if update["update_id"] == 1:
            raise KeyError("boom")
        real(update)

    monkeypatch.setattr(bot, "handle_update", flaky)
    tg.queue["getUpdates"] = [updates(msg("/help", uid=1), msg("/start t1", uid=2), "junk")]
    assert bot.poll_once() == 2
    assert calls == [1, 2] and store.load_bot_offset() == 3
    assert store.active_subscribers("ai_news") == [CHAT]


def test_network_errors_back_off_without_sleeping(
    tg: TgHttp, store: Store, ticker: Ticker, log: io.StringIO
) -> None:
    waits: list[float] = []
    bot = make_bot(tg, store, ticker, log, wait=waits.append)
    net = HttpError(f"request to {API}getUpdates failed", status=None)
    tg.queue["getUpdates"] = [
        net,
        HttpError("bad gateway", status=502),
        {"ok": False},
        net,
        net,
        net,
        net,
        updates(),
        net,
    ]
    assert bot.run(max_polls=9) == 9
    assert waits == [1, 2, 4, 8, 16, 30, 30, 1]  # capped at 30 s, reset after a success
    out = log.getvalue()
    assert "network error" in out and "HTTP 502" in out and TOKEN not in out


def test_conflict_hint_and_stop_event(
    tg: TgHttp, store: Store, ticker: Ticker, log: io.StringIO
) -> None:
    bot = make_bot(tg, store, ticker, log)
    bot.wait = lambda seconds: bot.stop.set()  # stop during the first back-off
    tg.queue["getUpdates"] = [HttpError("conflict", status=409)]
    assert bot.run() == 1
    assert "another bot instance" in log.getvalue() and "stopped" in log.getvalue()


def test_sigterm_stops_loop(bot: SubscriptionBot) -> None:
    previous = {s: signal.getsignal(s) for s in (signal.SIGTERM, signal.SIGINT)}
    try:
        bot.install_signal_handlers()
        os.kill(os.getpid(), signal.SIGTERM)
        assert bot.stop.is_set()
        assert bot.run() == 0  # exits without polling
    finally:
        for sig, handler in previous.items():
            signal.signal(sig, handler)


def test_rejects_bad_token(store: Store) -> None:
    with pytest.raises(ValueError):
        SubscriptionBot("nope", TgHttp(), store, TOPICS, FixedClock(MONDAY))


def test_bot_uses_stable_bits_not_positions(
    tg: TgHttp, store: Store, ticker: Ticker, log: io.StringIO
) -> None:
    shuffled = [t for t in TOPICS if t.id != "security"][::-1]  # reordered, one removed
    bot = SubscriptionBot(
        TOKEN, tg, store, shuffled, FixedClock(MONDAY), monotonic=ticker, public_signup=True
    )
    bot.handle_update(msg("/start t40"))
    assert store.get_subscriber(CHAT).topics == ("ai_models",)  # type: ignore[union-attr]
    bot.handle_update(msg("/start t3", uid=2))  # bit 1 (security) is gone -> picker
    picker = tg.of("sendMessage")[-1]["reply_markup"]
    assert picker["inline_keyboard"][0][0]["text"].startswith("✅ 🤖")  # ai_models listed first
    assert picker["inline_keyboard"][0][0]["callback_data"] == "tog:40:ai_models"
    bot.handle_update(cb("tog:40:ai_news", uid=3))
    edited = tg.of("editMessageReplyMarkup")[-1]["reply_markup"]
    assert edited["inline_keyboard"][-1][0]["callback_data"] == "save:41"
    bot.handle_update(cb("save:2", uid=4))  # bit 1 no longer owned
    assert tg.of("answerCallbackQuery")[-1]["text"] == EXPIRED
    bot.handle_update(cb("save:41", uid=5))
    assert store.get_subscriber(CHAT).topics == ("ai_models", "ai_news")  # type: ignore[union-attr]
