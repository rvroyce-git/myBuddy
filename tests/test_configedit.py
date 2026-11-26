"""Owner config changes from the bot: preview, approval, expiry, undo and scheduler reload."""

from __future__ import annotations

import io
import re
import shutil
import stat
import threading
from collections.abc import Iterator
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest
import yaml
from conftest import MONDAY, FixedClock
from test_bot import OWNER, TOKEN, TOPICS, TgHttp, Ticker, msg
from test_bot_access import CHAT_TIME

from mybuddy.bot import OWNER_ONLY, PRIVATE, SubscriptionBot
from mybuddy.chat import ChatResponder
from mybuddy.config import DEFAULT_CONFIG_PATH, Config, ConfigError, Settings, load_config
from mybuddy.config import DEFAULT_CONFIG_PATH as _SHIPPED
from mybuddy.config import load_topics as _load
from mybuddy.configedit import (
    APPROVAL_SECONDS,
    BACKUP_DIR,
    RELOAD_MARKER,
    ConfigEditor,
    make_reloader,
)
from mybuddy.schedule import Scheduler
from mybuddy.store import Store

SHIPPED_TOPICS = len(_load(_SHIPPED))  # count from the real config, not a hard-coded number

FRIEND = 444555666
STRANGER = 999888777


@pytest.fixture
def store(data_dir: Path) -> Iterator[Store]:
    s = Store(data_dir)
    yield s
    s.close()


@pytest.fixture
def cfg(tmp_path: Path) -> Path:
    folder = tmp_path / "conf"
    folder.mkdir()
    path = folder / "topics.yaml"
    shutil.copy(DEFAULT_CONFIG_PATH, path)
    path.chmod(0o600)
    return path


@pytest.fixture
def clock() -> FixedClock:
    return FixedClock(MONDAY)


@pytest.fixture
def tg() -> TgHttp:
    return TgHttp()


@pytest.fixture
def bot(tg: TgHttp, store: Store, cfg: Path, clock: FixedClock, data_dir: Path) -> SubscriptionBot:
    editor = ConfigEditor(cfg, store, clock, marker_path=data_dir / RELOAD_MARKER)
    settings = Settings(groq_api_key="gsk_" + "k" * 30, telegram_bot_token=TOKEN)
    return SubscriptionBot(
        TOKEN,
        tg,
        store,
        TOPICS,
        clock,
        owner_chat_id=str(OWNER),
        chat_allowlist=(FRIEND,),
        monotonic=Ticker(),
        log=io.StringIO(),
        chat=ChatResponder(settings, tg, store, TOPICS, FixedClock(CHAT_TIME)),
        editor=editor,
    )


def say(bot: SubscriptionBot, tg: TgHttp, text: str, chat: int = OWNER) -> str:
    bot.handle_update(msg(text, chat=chat))
    return tg.texts()[-1]


def change_id(reply: str) -> str:
    match = re.search(r"Change ([0-9a-f]{6}):", reply)
    assert match, reply
    return match.group(1)


def topic(path: Path, topic_id: str) -> dict[str, Any]:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    return next(t for t in data["topics"] if t["id"] == topic_id)


def test_topic_off_preview_then_approve(
    bot: SubscriptionBot, tg: TgHttp, cfg: Path, data_dir: Path
) -> None:
    original = cfg.read_text(encoding="utf-8")
    reply = say(bot, tg, "/topic off ai_news")
    cid = change_id(reply)
    assert "+  enabled: false" in reply and "--- topics.yaml" in reply
    assert f"Validation: ok ({SHIPPED_TOPICS} topics)" in reply and f"/approve {cid}" in reply
    assert cfg.read_text(encoding="utf-8") == original  # nothing written yet
    assert not (data_dir / RELOAD_MARKER).exists()

    reply = say(bot, tg, f"/approve {cid}")
    assert reply.startswith(f"Applied {cid}: topic off ai_news")
    assert topic(cfg, "ai_news")["enabled"] is False
    assert stat.S_IMODE(cfg.stat().st_mode) == 0o600
    [backup] = (cfg.parent / BACKUP_DIR).iterdir()
    assert backup.read_text(encoding="utf-8") == original
    assert stat.S_IMODE(backup.stat().st_mode) == 0o600
    assert (data_dir / RELOAD_MARKER).exists()
    assert not load_config(cfg).topics[0].enabled

    changes = say(bot, tg, "/changes")
    assert f"{cid} applied: topic off ai_news" in changes
    assert f"{cid} proposed: topic off ai_news" in changes
    assert say(bot, tg, f"/approve {cid}").startswith("No such pending change")  # once only


def test_set_alias_time_and_reject(bot: SubscriptionBot, tg: TgHttp, cfg: Path) -> None:
    reply = say(bot, tg, "/set time security 09:05")
    assert "-    time: 07:00" in reply and "+    time: 09:05" in reply, reply
    cid = change_id(reply)
    assert say(bot, tg, f"/reject {cid}") == f"Rejected {cid}. Nothing changed."
    assert say(bot, tg, f"/approve {cid}").startswith("No such pending change")
    assert topic(cfg, "security")["schedule"]["time"] == "07:00"
    cid = change_id(say(bot, tg, "/time security 09:05"))
    say(bot, tg, f"/approve {cid}")
    assert topic(cfg, "security")["schedule"]["time"] == "09:05"


def test_approval_expires_after_ten_minutes(
    bot: SubscriptionBot, tg: TgHttp, cfg: Path, clock: FixedClock
) -> None:
    original = cfg.read_text(encoding="utf-8")
    cid = change_id(say(bot, tg, "/topic off security"))
    clock.advance(seconds=APPROVAL_SECONDS + 1)
    assert say(bot, tg, f"/approve {cid}").startswith("No such pending change")
    assert cfg.read_text(encoding="utf-8") == original
    assert f"{cid} expired" in say(bot, tg, "/changes")


def test_non_owner_cannot_change_config(
    bot: SubscriptionBot, tg: TgHttp, cfg: Path, store: Store
) -> None:
    original = cfg.read_text(encoding="utf-8")
    for text in (
        "/topic off ai_news",
        "/config",
        "/limit chat-daily 999",
        "/undo",
        "/approve abcdef",
    ):
        assert say(bot, tg, text, chat=FRIEND) == OWNER_ONLY  # allowlisted is not enough
    assert say(bot, tg, "/topic off ai_news", chat=STRANGER) == PRIVATE
    assert cfg.read_text(encoding="utf-8") == original
    assert store.config_changes() == []


@pytest.mark.parametrize(
    ("text", "why"),
    [
        ("/source remove github_trending GitHub Trending", "would be invalid"),
        ("/time kev_alerts 07:00", "only fits daily topics"),
        ("/time ai_news 25:00", "HH:MM"),
        ("/topic off no_such_topic", "no topic with id"),
        ("/topic sideways ai_news", "use /topic on"),
        ("/topic on ai_news", "already the current setting"),
        ("/set GROQ_API_KEY gsk_abc", "unknown change"),
        ("/set TELEGRAM_BOT_TOKEN x", "unknown change"),
        ("/source add deep_dive https://example.org/feed", "rotation"),
        ("/source add ai_news http://example.org/feed", "https://"),
        ("/source add ai_news https://10.0.0.1/feed", "not an IP"),
        ("/source add ai_news https://example.org:8443/feed", "no port"),
        ("/source add ai_news https://user@example.org/feed", "no port or login"),
        ("/source add ai_news https://openai.com/news/rss.xml", "already a source"),
        ("/limit chat-daily 5000", "between 0 and 1000"),
        ("/limit chat-tokens 80001", "between 0 and 80000"),
        ("/limit chat-tokens 150000", "between 0 and 80000"),
        ("/limit groq-key 1", "use /limit"),
    ],
)
def test_invalid_requests_write_nothing(
    bot: SubscriptionBot, tg: TgHttp, cfg: Path, data_dir: Path, text: str, why: str
) -> None:
    original = cfg.read_text(encoding="utf-8")
    reply = say(bot, tg, text)
    assert reply.startswith("Not changed:") and why in reply, reply
    assert cfg.read_text(encoding="utf-8") == original
    assert not (cfg.parent / BACKUP_DIR).exists() and not (data_dir / RELOAD_MARKER).exists()
    assert not re.search(r"Change [0-9a-f]{6}:", reply)


def test_source_add_adds_allowed_host_without_fetching(
    bot: SubscriptionBot, tg: TgHttp, cfg: Path
) -> None:
    calls = len(tg.calls)
    reply = say(bot, tg, "/source add security https://news.example.org/feed.xml")
    assert "name: news.example.org" in reply and "https://news.example.org/feed.xml" in reply
    assert "+- news.example.org" in reply and "Nothing was fetched" in reply
    assert [m for m, _ in tg.calls[calls:]] == ["sendMessage"]  # only the reply went out
    say(bot, tg, f"/approve {change_id(reply)}")
    config = load_config(cfg)
    assert "news.example.org" in config.extra_allowed_hosts
    sec = next(t for t in config.topics if t.id == "security")
    assert sec.sources[-1] == {
        "type": "rss",
        "name": "news.example.org",
        "url": "https://news.example.org/feed.xml",
    }
    cid = change_id(say(bot, tg, "/source remove security news.example.org"))
    say(bot, tg, f"/approve {cid}")
    assert len(next(t for t in load_config(cfg).topics if t.id == "security").sources) == 3


def test_undo_restores_backup_with_approval(bot: SubscriptionBot, tg: TgHttp, cfg: Path) -> None:
    original = cfg.read_text(encoding="utf-8")
    assert "no backup to restore" in say(bot, tg, "/undo")
    say(bot, tg, f"/approve {change_id(say(bot, tg, '/topic off startups'))}")
    reply = say(bot, tg, "/undo")
    assert "undo: restore topics.yaml." in reply and "-  enabled: false" in reply
    assert topic(cfg, "startups")["enabled"] is False  # not yet
    say(bot, tg, f"/approve {change_id(reply)}")
    assert cfg.read_text(encoding="utf-8") == original  # comments and all


def test_config_edited_since_preview_is_not_overwritten(
    bot: SubscriptionBot, tg: TgHttp, cfg: Path
) -> None:
    cid = change_id(say(bot, tg, "/topic off ai_news"))
    edited = cfg.read_text(encoding="utf-8") + "\n# edited by hand\n"
    cfg.write_text(edited, encoding="utf-8")
    assert "changed since the preview" in say(bot, tg, f"/approve {cid}")
    assert cfg.read_text(encoding="utf-8") == edited


def test_limit_change_is_an_approved_override(
    bot: SubscriptionBot, tg: TgHttp, store: Store, cfg: Path
) -> None:
    original = cfg.read_text(encoding="utf-8")
    reply = say(bot, tg, "/limit chat-daily 50")
    assert "CHAT_DAILY_CAP: the setting's value -> 50" in reply and "env file is not" in reply
    assert store.limit_overrides() == {}
    say(bot, tg, f"/approve {change_id(reply)}")
    assert store.limit_overrides() == {"chat_daily_cap": 50}
    assert bot.chat is not None and bot.chat.limits()["chat_daily_cap"] == 50
    assert cfg.read_text(encoding="utf-8") == original
    assert "50/day per chat" in say(bot, tg, "/config")


def test_config_shows_safe_settings_only(bot: SubscriptionBot, tg: TgHttp) -> None:
    reply = say(bot, tg, "/config")
    assert "• ai_news: on, Daily · 06:30 IST, 4 source(s)" in reply
    assert "• kev_alerts: on, Every hour" in reply
    assert "Signup: private; 2 allowed chat(s)" in reply
    assert TOKEN not in reply and "gsk_" not in reply and str(OWNER) not in reply
    assert "/topic on|off" in say(bot, tg, "/set help")


def test_editor_off_when_not_configured(tg: TgHttp, store: Store) -> None:
    bot = SubscriptionBot(
        TOKEN, tg, store, TOPICS, FixedClock(MONDAY), owner_chat_id=str(OWNER), monotonic=Ticker()
    )
    assert "aren't set up" in say(bot, tg, "/topic off ai_news")


# --------------------------------------------------------------------------- reload


def test_reloader_reads_marker_once_and_skips_invalid(cfg: Path, data_dir: Path) -> None:
    marker = data_dir / RELOAD_MARKER
    data_dir.mkdir(parents=True, exist_ok=True)
    seen: list[Config] = []
    log = io.StringIO()
    reload = make_reloader(cfg, marker, load_config, seen.append, log=log)
    assert reload() is None and seen == []
    marker.write_text("x")
    topics = reload()
    assert topics is not None and len(topics) == SHIPPED_TOPICS and len(seen) == 1
    assert not marker.exists() and reload() is None
    cfg.write_text("topics: nope\n", encoding="utf-8")
    marker.write_text("x")
    assert reload() is None and not marker.exists() and len(seen) == 1
    assert "config reload skipped" in log.getvalue()


def test_scheduler_picks_up_reload_and_skips_disabled() -> None:
    clock = FixedClock(MONDAY.replace(hour=6, minute=0))
    a = next(t for t in TOPICS if t.id == "ai_news")  # 06:30
    b = next(t for t in TOPICS if t.id == "security")  # 07:00
    ran: list[tuple[str, str]] = []
    pending: list[list[Any]] = []

    class Stop(threading.Event):
        def wait(self, timeout: float | None = None) -> bool:
            assert timeout is not None and timeout <= 60  # polls at least once a minute
            clock.advance(seconds=timeout)
            return False

    def reload() -> list[Any] | None:
        return pending.pop() if pending else None

    def run(t: Any) -> None:
        ran.append((t.id, clock.now().strftime("%H:%M")))
        if t.id == "ai_news":  # an approved change: ai_news off, security moved to 07:10
            from dataclasses import replace

            from mybuddy.config import Schedule

            pending.append([replace(a, enabled=False), replace(b, schedule=Schedule(time="07:10"))])

    log = io.StringIO()
    sched = Scheduler([a, b], run, clock, stop=Stop(), log=log, reload=reload)
    assert sched.loop(max_runs=3) == 3
    assert ran[0] == ("ai_news", "06:30")
    assert ran[1] == ("security", "07:10") and ran[2][0] == "security"  # ai_news never again
    assert "config reloaded: 2 topics" in log.getvalue() and "ai_news: disabled" in log.getvalue()


def test_disabled_topic_is_valid_config(cfg: Path) -> None:
    text = cfg.read_text(encoding="utf-8").replace("bit: 0 ", "enabled: false\n    bit: 0 ", 1)
    cfg.write_text(text, encoding="utf-8")
    assert not load_config(cfg).topics[0].enabled
    cfg.write_text(text.replace("enabled: false", "enabled: maybe"), encoding="utf-8")
    with pytest.raises(ConfigError, match="enabled: must be true or false"):
        load_config(cfg)


def test_backups_are_bounded(
    bot: SubscriptionBot, tg: TgHttp, cfg: Path, clock: FixedClock
) -> None:
    ticker = Ticker()
    bot.monotonic = ticker  # stay under the per-chat flood guard
    for i in range(22):
        state = "off" if i % 2 == 0 else "on"
        say(bot, tg, f"/approve {change_id(say(bot, tg, f'/topic {state} ai_models'))}")
        clock.advance(seconds=1)
        ticker.t += 61
    assert len(list((cfg.parent / BACKUP_DIR).iterdir())) == 20
    assert timedelta(seconds=22) == clock.now() - MONDAY


def test_cli_editor_needs_a_writable_config(cfg: Path, store: Store, clock: FixedClock) -> None:
    from mybuddy import cli

    settings = Settings(data_dir=cfg.parent)
    assert cli._config_editor(None, store, clock, settings) is None
    assert cli._config_editor(cfg.parent / "missing.yaml", store, clock, settings) is None
    editor = cli._config_editor(cfg, store, clock, settings)
    assert editor is not None and editor.marker_path == cfg.parent / RELOAD_MARKER
    cfg.chmod(0o400)
    try:
        assert cli._config_editor(cfg, store, clock, settings) is None
    finally:
        cfg.chmod(0o600)


def test_serve_wires_reload(monkeypatch: pytest.MonkeyPatch, cfg: Path, tmp_path: Path) -> None:
    from mybuddy import cli

    seen: dict[str, Any] = {}

    def fake_loop(self: Scheduler, max_runs: int | None = None) -> int:
        seen["reload"] = self.reload
        seen["max_sleep"] = self.max_sleep
        return 0

    monkeypatch.setattr(Scheduler, "loop", fake_loop)
    monkeypatch.setattr(Scheduler, "install_signal_handlers", lambda self: None)
    settings = Settings(data_dir=tmp_path / "d", site_data=tmp_path / "r.json", fake=True)
    topics = load_config(cfg).topics
    assert cli.cmd_serve(topics, settings, FixedClock(MONDAY), frozenset(), cfg) == 0
    assert seen["reload"] is not None and seen["max_sleep"] <= 60
    (tmp_path / "d" / RELOAD_MARKER).write_text("x")
    assert seen["reload"]() is not None and not (tmp_path / "d" / RELOAD_MARKER).exists()
