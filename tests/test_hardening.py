"""Hostile-input and secret-safety checks from the security review (one test per fix)."""

from __future__ import annotations

import http.client
import io
import os
import stat
import urllib.request
from email.message import Message
from pathlib import Path
from typing import Any

import pytest
from conftest import FakeHttp, FakeSource, FixedClock, make_item, make_topic
from test_cli import env  # noqa: F401  (pytest fixture)
from test_engine import make_deps
from test_engine_http import TEST_HOSTS, FakeResponse, client
from test_sources_huggingface import models

from mybuddy import cli
from mybuddy import http as http_mod
from mybuddy.config import (
    DEFAULT_MAX_SUBSCRIBERS,
    ConfigError,
    Settings,
    load_env_file,
    load_topics,
    parse_max_subscribers,
    parse_topics,
)
from mybuddy.deliver import format_telegram
from mybuddy.engine import MAX_URL_LENGTH, deliverable_url, run_topic
from mybuddy.export import _contract_report
from mybuddy.http import HttpError, UrllibHttpClient, _HttpsOnlyRedirect
from mybuddy.models import Report, ReportItem
from mybuddy.sources._common import parse_iso
from mybuddy.sources.github_trending import GitHubTrendingSource
from mybuddy.sources.reddit import RedditSource
from mybuddy.store import Store
from mybuddy.summarize import GROQ_URL, Provider, Summarizer

TOKEN = "123456789:AAH-secret_token_value_1234567890"

# ----------------------------------------------------------------------------- http


def test_unwrapped_http_exceptions_never_leak_the_url() -> None:
    url = f"https://api.telegram.org/bot{TOKEN}/getMe"
    for exc in (
        http.client.InvalidURL(f"URL can't contain control characters. {url!r}"),
        http.client.IncompleteRead(b"partial"),
        ValueError(f"bad {url}"),
    ):
        c, _ = client(exc)
        with pytest.raises(HttpError) as info:
            c.get_json(url)
        assert TOKEN not in str(info.value) and "secret" not in info.value.url


def test_rejects_control_characters_and_malformed_urls() -> None:
    c, opener = client(FakeResponse(b"{}", "https://x.example/"))
    for bad in (f"https://api.telegram.org/bot{TOKEN}\n/getMe", "https://[bad/", "https://a b/"):
        with pytest.raises(HttpError) as info:
            c.get_bytes(bad)
        assert TOKEN not in str(info.value)
    assert opener.requests == []


@pytest.mark.parametrize(
    "url",
    [
        "https://127.0.0.1/",
        "https://localhost/x",
        "https://api.localhost/",
        "https://[::1]/",
        "https://10.1.2.3/",
        "https://169.254.169.254/latest/meta-data",
        "https://2130706433/",
        "https://127.1/",
    ],
)
def test_refuses_internal_addresses(url: str) -> None:
    """Not on the allowlist, so refused before any connection (requests and redirects)."""
    c, opener = client(FakeResponse(b"", url))
    with pytest.raises(HttpError, match="host not allowed"):
        c.get_bytes(url)
    with pytest.raises(HttpError, match="host not allowed"):
        _HttpsOnlyRedirect(TEST_HOSTS).redirect_request(
            urllib.request.Request("https://ok.example/"),
            None,  # type: ignore[arg-type]
            302,
            "Found",
            Message(),  # type: ignore[arg-type]
            url,
        )
    assert opener.requests == []


def test_cross_host_redirect_drops_credentials() -> None:
    req = urllib.request.Request(
        "https://api.groq.com/x", headers={"Authorization": "Bearer k", "Accept": "a"}
    )
    handler = _HttpsOnlyRedirect(TEST_HOSTS)
    hdrs = Message()
    other = handler.redirect_request(req, None, 302, "Found", hdrs, "https://cdn.example/")  # type: ignore[arg-type]
    assert other is not None and not other.has_header("Authorization")
    assert other.get_header("Accept") == "a"
    same = handler.redirect_request(req, None, 302, "Found", hdrs, "https://api.groq.com/y")  # type: ignore[arg-type]
    assert same is not None and same.get_header("Authorization") == "Bearer k"


class _DripResponse(FakeResponse):
    """Sends one byte per read, as a slow-drip server would."""

    def read(self, n: int | None = -1) -> bytes:
        return super().read(1)


def test_slow_drip_response_hits_total_deadline() -> None:
    ticks = iter(range(10_000))
    c = UrllibHttpClient(
        allowed_hosts=TEST_HOSTS, timeout=1.0, monotonic=lambda: float(next(ticks))
    )
    c._opener = type(
        "O", (), {"open": lambda self, r, timeout: _DripResponse(b"x" * 100, "https://s.example/")}
    )()  # type: ignore[assignment]
    with pytest.raises(HttpError, match="too long"):
        c.get_bytes("https://s.example/")


def test_safe_url_redacts_discord_webhook_and_survives_garbage() -> None:
    url = "https://discord.com/api/webhooks/123456789012345678/AbCdEfSecretTokenValue"
    assert "AbCd" not in http_mod._safe_url(url)
    assert "<redacted>" in http_mod._safe_url(url)
    assert http_mod._safe_url("https://[bad/") == "<malformed URL>"


def test_deeply_nested_json_is_an_http_error() -> None:
    c, _ = client(FakeResponse(b"[" * 200_000, "https://api.example/"))
    with pytest.raises(HttpError, match="invalid JSON"):
        c.get_json("https://api.example/")


def test_retry_after_nan_is_ignored() -> None:
    assert http_mod._parse_retry_after("nan") is None
    assert http_mod._parse_retry_after("3") == 3.0


# ----------------------------------------------------------------------------- secrets


def test_settings_and_provider_repr_hide_secrets() -> None:
    s = Settings(
        groq_api_key="gsk_SECRET",
        gemini_api_key="gem_SECRET",
        discord_webhook_url="https://discord.com/api/webhooks/1/SECRET",
        telegram_bot_token=TOKEN,
        telegram_chat_id="987654321",
    )
    text = repr(s)
    assert "SECRET" not in text and TOKEN not in text and "987654321" not in text
    assert "SECRET" not in repr(Provider("groq", GROQ_URL, "gsk_SECRET", "m"))


def test_provider_error_body_never_echoes_the_key(
    settings: Settings, http: FakeHttp, clock: FixedClock
) -> None:
    log = io.StringIO()
    http.post_responses = [
        HttpError("HTTP 401", status=401, body='{"error": "bad key gsk_test\\n given"}')
    ]
    s = Summarizer(settings, http, clock, sleep=lambda _: None, log=log)
    report = s.summarize(make_topic(), [make_item(1)])
    assert report.summary.startswith("AI summary unavailable")
    assert "gsk_test" not in log.getvalue() and "<redacted>" in log.getvalue()


def test_missing_prompt_file_falls_back(
    settings: Settings, http: FakeHttp, clock: FixedClock, tmp_path: Path
) -> None:
    s = Summarizer(settings, http, clock, prompts_dir=tmp_path, log=io.StringIO())
    report = s.summarize(make_topic(), [make_item(1)])
    assert report.summary.startswith("AI summary unavailable") and http.calls == []


# ----------------------------------------------------------------------------- config


def test_max_subscribers_rejects_non_ascii_digits() -> None:
    for raw in ("²", "١٢", "-1", "10001", "1e3"):
        with pytest.raises(ConfigError):
            parse_max_subscribers(raw)
    assert parse_max_subscribers(" 42 ") == 42
    assert Settings.from_env({"MYBUDDY_MAX_SUBSCRIBERS": "²"}).max_subscribers == (
        DEFAULT_MAX_SUBSCRIBERS
    )


def test_unreadable_env_and_config_files_are_config_errors(tmp_path: Path) -> None:
    bad = tmp_path / "bad"
    bad.write_bytes(b"\xff\xfeKEY=\xff\n")
    with pytest.raises(ConfigError, match="cannot read env file"):
        load_env_file(bad, {})
    with pytest.raises(ConfigError, match="cannot read config"):
        load_topics(bad)


def test_schedule_time_must_be_exact_ascii(tmp_path: Path) -> None:
    def topics(time: str) -> Any:
        return {
            "topics": [
                {
                    "id": "kev",
                    "title": "KEV",
                    "emoji": "🚨",
                    "description": "d",
                    "schedule": {"time": time},
                    "ai": False,
                    "sources": [{"type": "cisa_kev", "name": "KEV"}],
                }
            ]
        }

    assert parse_topics(topics("06:30"))[0].schedule.time == "06:30"
    for bad in ("06:30\n", "٠٦:٣٠"):
        with pytest.raises(ConfigError, match="HH:MM"):
            parse_topics(topics(bad))


# ----------------------------------------------------------------------------- store/export


def test_damaged_report_row_does_not_break_export(tmp_path: Path) -> None:
    with Store(tmp_path / "data") as store:
        store._db.execute(
            "INSERT INTO reports(topic_id, created_at, json) VALUES ('t', 'x', '{broken')"
        )
        assert store.latest_report("t") is None
    assert _contract_report({"headline": "h", "items": 5})["items"] == []  # type: ignore[index]


# ----------------------------------------------------------------------------- sources


def test_parse_iso_overflow_is_none() -> None:
    assert parse_iso("0001-01-01T00:00:00+05:00") is None


def test_github_damaged_snapshot_value_is_a_newcomer() -> None:
    class Http:
        def get_json(self, url: str) -> Any:
            return {
                "items": [
                    {
                        "full_name": "a/b",
                        "html_url": "https://github.com/a/b",
                        "stargazers_count": 10,
                    }
                ]
            }

    state: dict[str, Any] = {"a/b": "lots"}
    src = GitHubTrendingSource(
        "GH", ["llm"], Http(), FixedClock(make_item(1).published), state=state
    )  # type: ignore[arg-type]
    [item] = src.collect()
    assert item.extra["stars_gained"] is None and state["a/b"] == 10


def test_ids_with_trailing_newline_are_rejected() -> None:
    with pytest.raises(ValueError, match="invalid subreddit"):
        RedditSource("R", "python\n", FakeHttp())  # type: ignore[arg-type]
    assert models([{"modelId": "org/model\n"}, {"modelId": "org/ok"}])[0].url == (
        "https://huggingface.co/org/ok"
    )


# ----------------------------------------------------------------------------- engine


def test_deliverable_url() -> None:
    assert deliverable_url("https://example.com/a?b=c")
    for bad in (
        "http://example.com/",
        "https://example.com/a\nb",
        "https://example.com/a b",
        "https:///nohost",
        "https://example.com/" + "a" * MAX_URL_LENGTH,
        None,
    ):
        assert not deliverable_url(bad)


def test_hostile_items_are_dropped_not_fatal(settings: Settings, clock: FixedClock) -> None:
    good = make_item(1, "A")
    hostile: list[Any] = [
        make_item(2, "A", url="https://evil.example/x\n<b>"),
        make_item(3, "A", url="https://evil.example/" + "a" * 5000),
        make_item(4, "A", title=None),
        "not an item",
        good,
    ]
    deps, _ = make_deps(settings, clock, {"A": FakeSource("A", hostile)})
    result = run_topic(make_topic(sources=({"type": "fake", "name": "A"},)), deps)
    assert result.status == "delivered"
    assert [i.url for i in result.report.items] == [good.url]  # type: ignore[union-attr]


# ----------------------------------------------------------------------------- delivery


def test_telegram_escapes_emoji() -> None:
    item = ReportItem("T", "https://x.example/", "S", "B")
    report = Report("t", "Title", make_item(1).published, "H", "", (item,))  # type: ignore[arg-type]
    assert format_telegram(report, "<b>").startswith("&lt;b&gt; <b>Title</b>")


# ----------------------------------------------------------------------------- cli


def _updates(*chats: dict[str, Any]) -> dict[str, Any]:
    return {"ok": True, "result": [{"update_id": 1, "message": {"chat": c}} for c in chats]}


def test_telegram_setup_ignores_non_integer_chat_ids(
    env: dict[str, Any],  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    env_file = env["tmp"] / "env"
    env_file.write_text("# keep\n", encoding="utf-8")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", TOKEN)
    url = f"https://api.telegram.org/bot{TOKEN}/getUpdates"
    env["http"].get_responses[url] = _updates({"id": "1\nGROQ_API_KEY=x", "type": "private"})
    argv = ["--config", env["argv"][1], "--env-file", str(env_file), "telegram-setup", "--save"]
    assert cli.main(argv) == 1
    assert env_file.read_text(encoding="utf-8") == "# keep\n"


def test_telegram_setup_replaces_exported_id_and_writes_0600(
    env: dict[str, Any],  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    env_file = env["tmp"] / "env"
    env_file.write_text("# keep\nexport TELEGRAM_CHAT_ID=5\n", encoding="utf-8")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "5")  # restored after the test
    os.chmod(env_file, 0o644)
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", TOKEN)
    url = f"https://api.telegram.org/bot{TOKEN}/getUpdates"
    env["http"].get_responses[url] = _updates({"id": 777, "type": "private"})
    argv = ["--config", env["argv"][1], "--env-file", str(env_file), "telegram-setup", "--save"]
    assert cli.main(argv) == 0
    assert env_file.read_text(encoding="utf-8") == "# keep\nTELEGRAM_CHAT_ID=777\n"
    assert stat.S_IMODE(env_file.stat().st_mode) == 0o600
    assert [p.name for p in env_file.parent.iterdir() if p.name.startswith(".env")] == []


def test_check_flags_open_env_file_bad_token_and_masks_chat_id(
    env: dict[str, Any],  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    env_file = env["tmp"] / "env"
    env_file.write_text("# secrets\n", encoding="utf-8")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "987654321")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "nope")
    os.chmod(env_file, 0o644)
    argv = ["--config", env["argv"][1], "--env-file", str(env_file), "check"]
    assert cli.main(argv) == 1
    out = capsys.readouterr().out
    assert "readable by other users" in out
    assert "does not look like a bot token" in out
    assert "987654321" not in out and "…321" in out
    assert env["http"].calls == []  # an invalid token is never put into a URL


def test_unwritable_data_dir_is_a_clean_error(
    env: dict[str, Any],  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    blocker = env["tmp"] / "file"
    blocker.write_text("", encoding="utf-8")
    monkeypatch.setenv("MYBUDDY_DATA_DIR", str(blocker / "data"))
    assert cli.main([*env["argv"], "subscribers"]) == 1
    assert "subscribers failed" in capsys.readouterr().err
