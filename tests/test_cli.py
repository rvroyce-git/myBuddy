from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import pytest
import yaml
from conftest import MONDAY, FakeHttp, FakeSource, FixedClock, make_item

import mybuddy.sources
from mybuddy import cli
from mybuddy.http import HttpError
from mybuddy.models import Clock, HttpClient, Source

CONFIG = {
    "topics": [
        {
            "id": "ai_news",
            "title": "AI news",
            "emoji": "🧠",
            "description": "AI things.",
            "schedule": {"time": "06:30", "days": "daily"},
            "ai": True,
            "prompt": "ai_news.md",
            "sources": [{"type": "rss", "name": "Feed", "url": "https://feed.example/rss"}],
        },
        {
            "id": "kev_alerts",
            "title": "KEV",
            "emoji": "🚨",
            "description": "Exploited bugs.",
            "schedule": {"every_minutes": 60},
            "ai": False,
            "sources": [{"type": "cisa_kev", "name": "KEV"}],
        },
    ]
}


@pytest.fixture
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    cfg = tmp_path / "topics.yaml"
    cfg.write_text(yaml.safe_dump(CONFIG, allow_unicode=True), encoding="utf-8")
    for name, _ in cli.SETTING_ENV_VARS:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("MYBUDDY_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("MYBUDDY_SITE_DATA", str(tmp_path / "site" / "reports.json"))
    http = FakeHttp()
    monkeypatch.setattr(cli, "make_http", lambda: http)
    monkeypatch.setattr(cli, "make_clock", lambda: FixedClock(MONDAY))

    def build(spec: dict[str, Any], h: HttpClient, c: Clock) -> Source:
        return FakeSource(spec["name"], [make_item(1, spec["name"])])

    monkeypatch.setattr(mybuddy.sources, "build_source", build)
    return {
        "argv": ["--config", str(cfg), "--env-file", str(tmp_path / "no-env")],
        "http": http,
        "tmp": tmp_path,
    }


def test_list(env: dict[str, Any], capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main([*env["argv"], "list"]) == 0
    out = capsys.readouterr().out
    assert "ai_news" in out and "Daily · 06:30 IST" in out and "Every hour" in out


def test_run_dry_run(env: dict[str, Any], capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main([*env["argv"], "run", "all", "--dry-run"]) == 0
    captured = capsys.readouterr()
    assert "(fake)" in captured.out and "🚨 **KEV**" in captured.out
    assert "ai_news: dry_run" in captured.err
    assert env["http"].calls == []
    assert not (env["tmp"] / "site" / "reports.json").exists()
    assert not (env["tmp"] / "data" / "outbox").exists()


def test_run_real_fake_mode_writes_outbox_and_export(
    env: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MYBUDDY_FAKE", "1")
    assert cli.main([*env["argv"], "run", "ai_news"]) == 0
    assert len(list((env["tmp"] / "data" / "outbox").iterdir())) == 1
    assert (env["tmp"] / "site" / "reports.json").exists()
    assert cli.main([*env["argv"], "run", "ai_news"]) == 0  # second run: nothing new
    assert len(list((env["tmp"] / "data" / "outbox").iterdir())) == 1
    assert cli.main([*env["argv"], "export"]) == 0


def test_run_unknown_topic(env: dict[str, Any]) -> None:
    assert cli.main([*env["argv"], "run", "nope"]) == 2


def test_bad_config_exit_code(env: dict[str, Any], tmp_path: Path) -> None:
    bad = tmp_path / "bad.yaml"
    bad.write_text("topics: []\n", encoding="utf-8")
    assert cli.main(["--config", str(bad), "list"]) == 2
    assert cli.main(["--config", str(bad), "check"]) == 1


def test_check_ok_hides_secrets(
    env: dict[str, Any], monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("GROQ_API_KEY", "gsk_supersecret")
    env["http"].get_responses[cli.GROQ_MODELS_URL] = {"data": [{"id": "openai/gpt-oss-120b"}]}
    assert cli.main([*env["argv"], "check"]) == 0
    out = capsys.readouterr().out
    assert "gsk_supersecret" not in out
    assert "GROQ_API_KEY         set" in out and "Groq reachable" in out
    [(_, _, _, headers)] = env["http"].calls
    assert headers == {"Authorization": "Bearer gsk_supersecret"}


def test_check_fails_when_groq_unreachable(
    env: dict[str, Any], monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("GROQ_API_KEY", "k")
    env["http"].get_responses[cli.GROQ_MODELS_URL] = HttpError("HTTP 401", status=401)
    assert cli.main([*env["argv"], "check"]) == 1
    assert "Groq check failed" in capsys.readouterr().out


def test_env_file_loaded(
    env: dict[str, Any], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    envfile = tmp_path / "env"
    envfile.write_text("MYBUDDY_FAKE=1\n", encoding="utf-8")
    monkeypatch.setattr(os, "environ", dict(os.environ))  # keep the env file's vars local
    argv = ["--config", env["argv"][1], "--env-file", str(envfile), "run", "ai_news"]
    assert cli.main(argv) == 0
    assert (tmp_path / "data" / "outbox").exists()  # fake mode came from the env file
