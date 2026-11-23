"""Telegram delivery: formatting, sending, retries, token safety and the setup command."""

from __future__ import annotations

import stat
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from conftest import FakeHttp, FixedClock
from test_cli import env  # noqa: F401  (pytest fixture)

from mybuddy import cli
from mybuddy.config import Settings
from mybuddy.deliver import (
    TELEGRAM_LIMIT,
    DeliveryError,
    DiscordWebhook,
    MultiDeliverer,
    OutboxWriter,
    TelegramBot,
    format_telegram,
    make_deliverer,
)
from mybuddy.http import HttpError
from mybuddy.models import Report, ReportItem

TOKEN = "123456789:AAH-secret_token_value_1234567890"
SEND_URL = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
WHEN = datetime(2026, 11, 23, 1, 0, tzinfo=UTC)


def report(n_items: int = 2, blurb: str = "Why it matters.") -> Report:
    items = tuple(
        ReportItem(f"Story {i}", f"https://news.example/{i}", "Example News", blurb)
        for i in range(1, n_items + 1)
    )
    return Report("ai_news", "AI news", WHEN, "Big day.", "Two things happened.", items)


def bot(http: FakeHttp, sleeps: list[float] | None = None) -> TelegramBot:
    log = sleeps if sleeps is not None else []
    return TelegramBot(TOKEN, "42", http, sleep=log.append, part_delay=0)


def test_format_is_escaped_html() -> None:
    r = Report(
        "security",
        "Security",
        WHEN,
        "Patch <now> & fast",
        "",
        (ReportItem("<script>x</script>", 'https://a.example/?q="1"&b=2', "A&B", "Use <b>"),),
    )
    text = format_telegram(r, "🛡️")
    assert text.startswith("🛡️ <b>Security</b>\n<b>Patch &lt;now&gt; &amp; fast</b>")
    assert '<a href="https://a.example/?q=&quot;1&quot;&amp;b=2">' in text
    assert "&lt;script&gt;x&lt;/script&gt;</a> · <i>A&amp;B</i>" in text
    assert "Use &lt;b&gt;" in text and "<script>" not in text


def test_sends_html_to_owner_chat() -> None:
    http = FakeHttp()
    bot(http).deliver(report(), "🧠")
    [(_, url, payload, _)] = http.posts
    assert url == SEND_URL
    assert payload["chat_id"] == "42" and payload["parse_mode"] == "HTML"
    assert payload["link_preview_options"] == {"is_disabled": True}
    assert '<a href="https://news.example/1">Story 1</a>' in payload["text"]


def test_long_report_is_split_under_limit() -> None:
    http = FakeHttp()
    bot(http).deliver(report(n_items=60, blurb="word " * 30), "🧠")
    assert len(http.posts) > 1
    assert all(len(p[2]["text"]) <= TELEGRAM_LIMIT for p in http.posts)
    # Each part is valid on its own: every <a> opened in a part is closed in it.
    assert all(p[2]["text"].count("<a ") == p[2]["text"].count("</a>") for p in http.posts)


def test_429_waits_and_retries() -> None:
    http = FakeHttp()
    body = '{"ok": false, "parameters": {"retry_after": 3}}'
    http.post_responses = [HttpError("429", status=429, body=body), {"ok": True}]
    sleeps: list[float] = []
    bot(http, sleeps).deliver(report(), "🧠")
    assert sleeps == [3.0] and len(http.posts) == 2


def test_errors_never_contain_the_token() -> None:
    http = FakeHttp()
    body = '{"ok": false, "description": "Bad Request: chat not found"}'
    http.post_responses = [HttpError(f"failed {SEND_URL}", url=SEND_URL, status=400, body=body)]
    with pytest.raises(DeliveryError) as info:
        bot(http).deliver(report(), "🧠")
    assert "chat not found" in str(info.value)
    assert TOKEN not in str(info.value) and "secret" not in str(info.value)


@pytest.mark.parametrize(("token", "chat"), [("not-a-token", "42"), (TOKEN, "@me"), (TOKEN, "")])
def test_rejects_bad_settings(token: str, chat: str) -> None:
    with pytest.raises(DeliveryError):
        TelegramBot(token, chat, FakeHttp())


def test_make_deliverer_picks_channels(tmp_path: Path) -> None:
    http, clock = FakeHttp(), FixedClock(WHEN)
    base = Settings(data_dir=tmp_path)
    tg = Settings(data_dir=tmp_path, telegram_bot_token=TOKEN, telegram_chat_id="42")
    both = Settings(
        data_dir=tmp_path,
        telegram_bot_token=TOKEN,
        telegram_chat_id="42",
        discord_webhook_url="https://discord.example/hook",
    )
    assert isinstance(make_deliverer(base, http, clock), OutboxWriter)
    assert isinstance(make_deliverer(tg, http, clock), TelegramBot)
    assert isinstance(make_deliverer(both, http, clock), MultiDeliverer)
    no_chat = Settings(data_dir=tmp_path, telegram_bot_token=TOKEN)
    assert isinstance(make_deliverer(no_chat, http, clock), OutboxWriter)
    fake = Settings(data_dir=tmp_path, telegram_bot_token=TOKEN, telegram_chat_id="42", fake=True)
    assert isinstance(make_deliverer(fake, http, clock), OutboxWriter)


def test_multi_tries_every_channel_then_fails() -> None:
    http = FakeHttp()
    http.post_responses = [HttpError("boom", status=500), {"ok": True}]
    discord = DiscordWebhook("https://discord.example/hook", http, sleep=lambda s: None)
    multi = MultiDeliverer([discord, bot(http)])
    with pytest.raises(DeliveryError, match="Discord"):
        multi.deliver(report(), "🧠")
    assert [p[1] for p in http.posts] == ["https://discord.example/hook", SEND_URL]


def _updates(*chats: dict[str, Any]) -> dict[str, Any]:
    return {
        "ok": True,
        "result": [{"update_id": i, "message": {"chat": c}} for i, c in enumerate(chats)],
    }


def test_telegram_setup_saves_chat_id(
    env: dict[str, Any],  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    env_file = env["tmp"] / "mybuddy.env"
    env_file.write_text("GROQ_API_KEY=x\nTELEGRAM_CHAT_ID=1\n", encoding="utf-8")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", TOKEN)
    env["http"].get_responses[f"https://api.telegram.org/bot{TOKEN}/getUpdates"] = _updates(
        {"id": 777, "type": "private", "first_name": "Alex"},
        {"id": -100, "type": "group", "title": "Some group"},
    )
    argv = ["--config", env["argv"][1], "--env-file", str(env_file), "telegram-setup", "--save"]
    assert cli.main(argv) == 0
    assert env_file.read_text(encoding="utf-8") == "GROQ_API_KEY=x\nTELEGRAM_CHAT_ID=777\n"
    assert stat.S_IMODE(env_file.stat().st_mode) == 0o600
    out = capsys.readouterr().out
    assert TOKEN not in out
    assert "private chat …" in out and "Alex" not in out


def test_telegram_setup_prints_masked_ids_only(
    env: dict[str, Any],  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", TOKEN)
    url = f"https://api.telegram.org/bot{TOKEN}/getUpdates"
    env["http"].get_responses[url] = _updates(
        {"id": 987654321, "type": "private", "first_name": "Alex", "username": "alex_k"}
    )
    assert cli.main([*env["argv"], "telegram-setup"]) == 0
    out = capsys.readouterr().out
    assert "private chat …321" in out and "--save" in out
    assert "987654321" not in out and "Alex" not in out and "alex_k" not in out
    env["http"].get_responses[url] = _updates(
        {"id": 987654321, "type": "private", "first_name": "Alex"},
        {"id": 123123123, "type": "private", "first_name": "Bea"},
    )
    assert cli.main([*env["argv"], "telegram-setup"]) == 1
    captured = capsys.readouterr()
    assert "…321" in captured.out and "…123" in captured.out and "Bea" not in captured.out
    assert "987654321" not in captured.out + captured.err


def test_telegram_setup_without_messages(
    env: dict[str, Any],  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", TOKEN)
    env["http"].get_responses[f"https://api.telegram.org/bot{TOKEN}/getUpdates"] = _updates()
    assert cli.main([*env["argv"], "telegram-setup"]) == 1
    assert "send 'hi'" in capsys.readouterr().err


def test_check_reports_telegram_bot(
    env: dict[str, Any],  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", TOKEN)
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "777")
    env["http"].get_responses[f"https://api.telegram.org/bot{TOKEN}/getMe"] = {
        "ok": True,
        "result": {"username": "mybuddy_news_bot"},
    }
    cli.main([*env["argv"], "check"])
    out = capsys.readouterr().out
    assert "ok: Telegram bot @mybuddy_news_bot" in out
    assert TOKEN not in out and "TELEGRAM_BOT_TOKEN   set" in out


def test_check_flags_rejected_token(
    env: dict[str, Any],  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", TOKEN)
    env["http"].get_responses[f"https://api.telegram.org/bot{TOKEN}/getMe"] = HttpError(
        f"401 from {TOKEN}", status=401
    )
    assert cli.main([*env["argv"], "check"]) == 1
    out = capsys.readouterr().out
    assert "Telegram check failed" in out and TOKEN not in out
