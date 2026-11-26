"""CLI: `mybuddy subscribers`, `mybuddy bot` and the subscriber lines of `mybuddy check`."""

from __future__ import annotations

from typing import Any

import pytest
from conftest import MONDAY
from test_cli import env  # noqa: F401  (pytest fixture)

import mybuddy.bot
from mybuddy import cli
from mybuddy.store import Store

TOKEN = "123456789:AAH-secret_token_value_1234567890"
CHATS = (987654321, 876543219, 765432198)


def _seed(env: dict[str, Any]) -> None:  # noqa: F811
    with Store(env["tmp"] / "data") as store:
        store.subscribe(CHATS[0], ["ai_news"], MONDAY)
        store.subscribe(CHATS[1], ["ai_news"], MONDAY)
        store.subscribe(CHATS[2], ["ai_news"], MONDAY)
        store.deactivate_subscriber(CHATS[2], MONDAY)


def _no_chat_ids(text: str) -> bool:
    return not any(str(c) in text or str(c)[:6] in text for c in CHATS)


def test_subscribers_counts_without_chat_ids(
    env: dict[str, Any],  # noqa: F811
    capsys: pytest.CaptureFixture[str],
) -> None:
    _seed(env)
    assert cli.main([*env["argv"], "subscribers"]) == 0
    out = capsys.readouterr().out
    assert "active subscribers: 2 (limit 100)" in out
    assert "ai_news" in out and "2" in out
    assert "(+ kev_alerts)" not in out  # the CLI test config has no audience key
    assert "https://t.me/myBuddy_update_bot?start=t1" in out
    assert _no_chat_ids(out)


def test_subscribers_on_empty_store(
    env: dict[str, Any],  # noqa: F811
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert cli.main([*env["argv"], "subscribers"]) == 0
    assert "active subscribers: 0" in capsys.readouterr().out


def test_check_reports_subscribers(
    env: dict[str, Any],  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _seed(env)
    monkeypatch.setenv("MYBUDDY_MAX_SUBSCRIBERS", "50")
    assert cli.main([*env["argv"], "check"]) == 0
    out = capsys.readouterr().out
    assert "subscribers: 2 active (limit 50)" in out
    assert "MYBUDDY_MAX_SUBSCRIBERS set (50)" in out
    assert "warning: MYBUDDY_MAX_SUBSCRIBERS" not in out
    assert _no_chat_ids(out)


def test_check_warns_on_invalid_limit(
    env: dict[str, Any],  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("MYBUDDY_MAX_SUBSCRIBERS", "lots")
    assert cli.main([*env["argv"], "check"]) == 0  # a warning, not a failure
    out = capsys.readouterr().out
    assert "warning: MYBUDDY_MAX_SUBSCRIBERS must be a whole number" in out
    assert "using 100" in out and "subscribers: 0 active (limit 100)" in out


def test_check_warns_on_bot_username_mismatch(
    env: dict[str, Any],  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", TOKEN)
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "777")
    url = f"https://api.telegram.org/bot{TOKEN}/getMe"
    env["http"].get_responses[url] = {"ok": True, "result": {"username": "myBuddy_update_bot"}}
    cli.main([*env["argv"], "check"])
    assert "website links point to" not in capsys.readouterr().out
    env["http"].get_responses[url] = {"ok": True, "result": {"username": "someone_else_bot"}}
    cli.main([*env["argv"], "check"])
    assert "website links point to @myBuddy_update_bot" in capsys.readouterr().out


def test_bot_needs_token_and_real_mode(
    env: dict[str, Any],  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert cli.main([*env["argv"], "bot"]) == cli.EX_CONFIG
    assert "TELEGRAM_BOT_TOKEN is missing" in capsys.readouterr().err
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", TOKEN)
    monkeypatch.setenv("MYBUDDY_FAKE", "1")
    assert cli.main([*env["argv"], "bot"]) == cli.EX_CONFIG
    assert "needs the real Telegram API" in capsys.readouterr().err


def test_bot_command_wires_settings(
    env: dict[str, Any],  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: dict[str, Any] = {}
    fake_http = object()

    def fake_run(self: mybuddy.bot.SubscriptionBot, max_polls: int | None = None) -> int:
        seen.update(
            owner=self.owner,
            limit=self.max_subscribers,
            name=self.bot_username,
            order=self.order,
            http=self.http,
        )
        return 0

    monkeypatch.setattr(mybuddy.bot.SubscriptionBot, "run", fake_run)
    monkeypatch.setattr(mybuddy.bot.SubscriptionBot, "install_signal_handlers", lambda self: None)
    bot_hosts: list[Any] = []
    monkeypatch.setattr(cli, "make_bot_http", lambda hosts: bot_hosts.append(hosts) or fake_http)
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", TOKEN)
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "777")
    monkeypatch.setenv("MYBUDDY_MAX_SUBSCRIBERS", "7")
    assert cli.main([*env["argv"], "bot"]) == 0
    assert seen == {
        "owner": 777,
        "limit": 7,
        "name": "myBuddy_update_bot",
        "order": ["ai_news"],
        "http": fake_http,
    }
    assert bot_hosts == [frozenset({"api.telegram.org"})]  # the bot only talks to Telegram


def test_bot_http_timeout_exceeds_poll() -> None:
    client = cli._bot_http(cli.TELEGRAM_HOSTS)
    assert client.timeout > mybuddy.bot.POLL_TIMEOUT_SECONDS + 5
    assert client.allowed_hosts == {"api.telegram.org"}


def test_check_reports_access_mode(
    env: dict[str, Any],  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert cli.main([*env["argv"], "check"]) == 0
    out = capsys.readouterr().out
    assert "mode: private (owner + allowlist only)" in out
    assert "chat allowlist: owner (not set) + 0" in out
    assert "chat: off (no AI key set)" in out

    monkeypatch.setenv("GROQ_API_KEY", "gsk_test")
    env["http"].get_responses[cli.GROQ_MODELS_URL] = {"data": [{"id": "openai/gpt-oss-120b"}]}
    monkeypatch.setenv("TELEGRAM_CHAT_ID", str(CHATS[0]))
    monkeypatch.setenv("CHAT_ALLOWLIST", f"{CHATS[1]},{CHATS[2]},nope")
    monkeypatch.setenv("PUBLIC_SIGNUP", "true")
    monkeypatch.setenv("CHAT_DAILY_CAP", "lots")
    assert cli.main([*env["argv"], "check"]) == 0
    out = capsys.readouterr().out
    assert "mode: public signup" in out and "chat allowlist: owner + 2" in out
    assert "CHAT_ALLOWLIST       set (2 id(s))" in out
    assert "warning: CHAT_ALLOWLIST has 1 invalid entry" in out
    assert "chat: on (2/min and 30/day per chat, 60000 tokens/day in total)" in out
    assert "warning: CHAT_DAILY_CAP must be a whole number" in out
    assert _no_chat_ids(out)

    monkeypatch.setenv("CHAT_ENABLED", "false")
    assert cli.main([*env["argv"], "check"]) == 0
    assert "chat: off (CHAT_ENABLED=false)" in capsys.readouterr().out


def test_bot_command_wires_access_and_chat(
    env: dict[str, Any],  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: dict[str, Any] = {}

    def fake_run(self: mybuddy.bot.SubscriptionBot, max_polls: int | None = None) -> int:
        seen.update(public=self.public_signup, allowed=self.allowed, chat=self.chat)
        return 0

    monkeypatch.setattr(mybuddy.bot.SubscriptionBot, "run", fake_run)
    monkeypatch.setattr(mybuddy.bot.SubscriptionBot, "install_signal_handlers", lambda self: None)
    monkeypatch.setattr(cli, "make_bot_http", lambda hosts: object())
    chat_hosts: list[Any] = []
    monkeypatch.setattr(cli, "make_chat_http", lambda hosts: chat_hosts.append(hosts) or object())
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", TOKEN)
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "777")
    monkeypatch.setenv("CHAT_ALLOWLIST", "888")
    assert cli.main([*env["argv"], "bot"]) == 0
    assert seen == {"public": False, "allowed": frozenset({777, 888}), "chat": None}
    assert chat_hosts == []  # no AI key: no chat client at all

    monkeypatch.setenv("GROQ_API_KEY", "gsk_test")
    monkeypatch.setenv("PUBLIC_SIGNUP", "1")
    assert cli.main([*env["argv"], "bot"]) == 0
    assert seen["public"] is True and seen["chat"] is not None
    assert chat_hosts == [frozenset({"api.groq.com"})]  # chat can reach only the AI provider


def test_chat_http_has_short_timeout() -> None:
    client = cli._chat_http(frozenset({"api.groq.com"}))
    assert client.timeout <= 20 and client.allowed_hosts == {"api.groq.com"}
