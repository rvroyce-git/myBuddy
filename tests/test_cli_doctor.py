"""mybuddy doctor: each check passes and fails with fakes (no real network or systemctl)."""

from __future__ import annotations

import json
import re
import shutil
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
import yaml
from conftest import MONDAY, FakeHttp, FakeSource, FixedClock, make_item

import mybuddy.sources
from mybuddy import cli
from mybuddy.cli import doctor
from mybuddy.deliver import TELEGRAM_API
from mybuddy.http import HttpError
from mybuddy.models import Clock, HttpClient, Source
from mybuddy.store import Store

TOKEN = "123456:" + "A" * 30
GROQ_KEY = "gsk_doctor_secret"
CONFIG = {
    "topics": [
        {
            "id": "ai_news",
            "title": "AI news",
            "emoji": "🧠",
            "description": "AI things.",
            "schedule": {"time": "06:30", "days": "daily"},
            "ai": True,
            "bit": 0,
            "prompt": "ai_news.md",
            "sources": [{"type": "rss", "name": "Feed", "url": "https://feed.example/rss"}],
        },
        {
            "id": "deep_dive",
            "title": "Deep dive",
            "emoji": "🔬",
            "description": "Weekdays.",
            "schedule": {"time": "07:30", "days": ["mon", "tue", "wed", "thu", "fri"]},
            "ai": True,
            "bit": 3,
            "prompt": "deep_dive.md",
            "sources": [{"type": "rss", "name": "Papers", "url": "https://papers.example/rss"}],
        },
    ]
}
MEMINFO_OK = "MemTotal:        1000000 kB\nMemFree:  100 kB\nMemAvailable:     500000 kB\n"


@pytest.fixture
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    cfg = tmp_path / "topics.yaml"
    cfg.write_text(yaml.safe_dump(CONFIG, allow_unicode=True), encoding="utf-8")
    cfg.chmod(0o600)
    for name, _ in cli.SETTING_ENV_VARS:
        monkeypatch.delenv(name, raising=False)
    data = tmp_path / "data"
    site = tmp_path / "site" / "reports.json"
    monkeypatch.setenv("MYBUDDY_DATA_DIR", str(data))
    monkeypatch.setenv("MYBUDDY_SITE_DATA", str(site))
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "4242")
    envfile = tmp_path / "env"
    envfile.write_text("# settings\n", encoding="utf-8")
    envfile.chmod(0o600)
    clock = FixedClock(MONDAY)
    http = FakeHttp()
    monkeypatch.setattr(cli, "make_http", lambda hosts: http)
    monkeypatch.setattr(cli, "make_clock", lambda: clock)
    meminfo = tmp_path / "meminfo"
    meminfo.write_text(MEMINFO_OK, encoding="ascii")
    monkeypatch.setattr(doctor, "MEMINFO", meminfo)
    free = {"bytes": 10 * 1024**3}
    monkeypatch.setattr(doctor, "disk_usage", lambda p: SimpleNamespace(free=free["bytes"]))
    units: dict[str, str] = {}
    monkeypatch.setattr(doctor, "which", lambda name: "/bin/systemctl" if units else None)
    monkeypatch.setattr(doctor, "systemctl_state", lambda unit: units.get(unit, "inactive"))
    # A healthy install: private data dir with runs recorded, fresh reports.json.
    with Store(data) as store:
        for topic_id in ("ai_news", "deep_dive"):
            store.record_run(topic_id, clock.now(), "delivered")
    site.parent.mkdir(parents=True)
    site.write_text(json.dumps({"generated_at": MONDAY.isoformat(), "topics": []}))
    return {
        "argv": ["--config", str(cfg), "--env-file", str(envfile), "doctor"],
        "http": http,
        "clock": clock,
        "data": data,
        "site": site,
        "envfile": envfile,
        "free": free,
        "units": units,
        "meminfo": meminfo,
        "tmp": tmp_path,
    }


def _doctor(env: dict[str, Any], capsys: pytest.CaptureFixture[str]) -> tuple[int, dict[str, str]]:
    code = cli.main(env["argv"])
    out = capsys.readouterr().out
    lines: dict[str, str] = {}
    for line in out.splitlines():
        m = re.match(r"(PASS|WARN|FAIL|SKIP)\s+(.+?)\s{2,}(.*)$", line)
        if m:
            lines[m.group(2)] = f"{m.group(1)} {m.group(3)}"
    assert TOKEN not in out and GROQ_KEY not in out
    return code, lines


def test_healthy_install_passes(env: dict[str, Any], capsys: pytest.CaptureFixture[str]) -> None:
    code, lines = _doctor(env, capsys)
    assert code == 0
    for name in (
        "config",
        "live config",
        "access",
        "sources",
        "env file",
        "data dir",
        "data writable",
        "database",
        "last run ai_news",
        "last run deep_dive",
        "publish",
        "host allowlist",
        "disk",
        "memory",
    ):
        assert lines[name].startswith("PASS"), (name, lines[name])
    assert lines["groq"].startswith("SKIP") and lines["telegram"].startswith("SKIP")
    assert lines["systemd"].startswith("SKIP")
    assert lines["env vars"].startswith("WARN")  # no AI key, no delivery channel
    assert "TELEGRAM_CHAT_ID" in lines["env vars"] and "4242" not in lines["env vars"]


def test_bad_config_fails(env: dict[str, Any], capsys: pytest.CaptureFixture[str]) -> None:
    Path(env["argv"][1]).write_text("topics: []\n", encoding="utf-8")
    code, lines = _doctor(env, capsys)
    assert code == 1 and lines["config"].startswith("FAIL")


def test_loose_permissions_fail(env: dict[str, Any], capsys: pytest.CaptureFixture[str]) -> None:
    env["envfile"].chmod(0o644)
    env["data"].chmod(0o755)
    code, lines = _doctor(env, capsys)
    assert code == 1
    assert lines["env file"].startswith("FAIL") and "chmod 600" in lines["env file"]
    assert lines["data dir"].startswith("FAIL") and "chmod 700" in lines["data dir"]


def test_missing_env_file_and_data_dir_warn(
    env: dict[str, Any], capsys: pytest.CaptureFixture[str]
) -> None:
    env["envfile"].unlink()
    shutil.rmtree(env["data"])
    code, lines = _doctor(env, capsys)
    assert code == 0
    assert lines["env file"].startswith("WARN") and lines["data dir"].startswith("WARN")
    assert lines["database"].startswith("WARN") and lines["last runs"].startswith("SKIP")
    assert not env["data"].exists()  # doctor never creates the data dir


def test_unwritable_data_dir_fails(
    env: dict[str, Any], capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    def refuse(*a: Any, **kw: Any) -> Any:
        raise PermissionError(13, "Permission denied")

    monkeypatch.setattr(doctor.tempfile, "TemporaryFile", refuse)
    code, lines = _doctor(env, capsys)
    assert code == 1 and lines["data writable"].startswith("FAIL")


def test_corrupt_database_fails(env: dict[str, Any], capsys: pytest.CaptureFixture[str]) -> None:
    for p in env["data"].glob("mybuddy.db*"):
        p.unlink()
    (env["data"] / "mybuddy.db").write_bytes(b"not a database" * 100)
    code, lines = _doctor(env, capsys)
    assert code == 1 and lines["database"].startswith("FAIL")


def test_stale_runs_fail(env: dict[str, Any], capsys: pytest.CaptureFixture[str]) -> None:
    env["clock"].advance(hours=30)
    code, lines = _doctor(env, capsys)
    assert code == 1
    assert lines["last run ai_news"].startswith("FAIL") and "stale" in lines["last run ai_news"]
    # Mon–Fri topics may rest over the weekend: 30 h is still fine for them.
    assert lines["last run deep_dive"].startswith("PASS")
    assert lines["publish"].startswith("WARN")


def test_never_ran_warns(env: dict[str, Any], capsys: pytest.CaptureFixture[str]) -> None:
    for p in env["data"].glob("mybuddy.db*"):
        p.unlink()
    Store(env["data"]).close()
    code, lines = _doctor(env, capsys)
    assert code == 0 and lines["last run ai_news"] == "WARN never ran"


def test_access_mode_is_reported_without_chat_ids(
    env: dict[str, Any], capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    code, lines = _doctor(env, capsys)
    assert (
        lines["access"].startswith("PASS") and "private (owner + allowlist only)" in lines["access"]
    )
    monkeypatch.setenv("CHAT_ALLOWLIST", "777001,nonsense")
    code, lines = _doctor(env, capsys)
    assert code == 0 and lines["access"].startswith("WARN")
    assert "1 invalid entry" in lines["access"] and "777001" not in lines["access"]


def test_live_config_checks(
    env: dict[str, Any], capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg = Path(env["argv"][1])
    cfg.chmod(0o644)
    assert _doctor(env, capsys)[1]["live config"].startswith("WARN")
    cfg.chmod(0o600)
    (env["data"] / "reload-config").write_text("x\n")
    assert "reload is pending" in _doctor(env, capsys)[1]["live config"]
    from mybuddy.config import DEFAULT_CONFIG_PATH  # noqa: PLC0415

    env["argv"][1] = str(DEFAULT_CONFIG_PATH)
    code, lines = _doctor(env, capsys)
    assert lines["config"].startswith("PASS") and "MYBUDDY_CONFIG" in lines["live config"]


def test_failed_sources_and_turned_off_topics(
    env: dict[str, Any], capsys: pytest.CaptureFixture[str]
) -> None:
    data = yaml.safe_load(Path(env["argv"][1]).read_text(encoding="utf-8"))
    data["topics"][1]["enabled"] = False
    Path(env["argv"][1]).write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")
    env["clock"].advance(hours=30)
    with Store(env["data"]) as store:
        store.record_source_failure("ai_news", "Feed", env["clock"].now(), "HTTP 500")
    code, lines = _doctor(env, capsys)
    assert code == 1  # stale topic fails; the failed source alone only warns
    assert lines["sources"].startswith("WARN") and "ai_news/Feed x1" in lines["sources"]
    assert lines["last run deep_dive"] == "SKIP turned off (enabled: false)"
    assert lines["last run ai_news"].startswith("FAIL")
    assert "1 topics on; 1 turned off" in lines["config"]


def test_health_report_itself_is_checked(
    env: dict[str, Any], capsys: pytest.CaptureFixture[str]
) -> None:
    data = yaml.safe_load(Path(env["argv"][1]).read_text(encoding="utf-8"))
    data["topics"].append(
        {
            "id": "health",
            "title": "Health check",
            "emoji": "🩺",
            "description": "The app's own state.",
            "schedule": {"time": "09:00"},
            "ai": False,
            "kind": "health",
            "website": False,
        }
    )
    Path(env["argv"][1]).write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")
    code, lines = _doctor(env, capsys)
    assert lines["config"].startswith("PASS"), lines["config"]
    assert lines["last run health"] == "WARN never ran"
    with Store(env["data"]) as store:
        store.record_run("health", env["clock"].now(), "delivered")
    assert _doctor(env, capsys)[1]["last run health"].startswith("PASS")
    env["clock"].advance(hours=30)
    assert "stale" in _doctor(env, capsys)[1]["last run health"]


def test_run_command_records_last_run(
    env: dict[str, Any], capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    for p in env["data"].glob("mybuddy.db*"):
        p.unlink()

    def build(spec: dict[str, Any], h: HttpClient, c: Clock) -> Source:
        return FakeSource(spec["name"], [make_item(1, spec["name"])])

    monkeypatch.setattr(mybuddy.sources, "build_source", build)
    monkeypatch.setenv("MYBUDDY_FAKE", "1")
    run_argv = [*env["argv"][:-1], "run", "ai_news"]
    assert cli.main(run_argv) == 0
    assert cli.main(run_argv) == 0  # nothing new the second time: still recorded
    with Store(env["data"]) as store:  # the engine's run log (shared with the health check)
        assert store.last_run("ai_news", ["no_new_items"]) == env["clock"].now()
    capsys.readouterr()
    _, lines = _doctor(env, capsys)
    assert lines["last run ai_news"].startswith("PASS")
    assert lines["last run deep_dive"] == "WARN never ran"


def test_publish_missing_or_broken(env: dict[str, Any], capsys: pytest.CaptureFixture[str]) -> None:
    env["site"].write_text("{broken", encoding="utf-8")
    code, lines = _doctor(env, capsys)
    assert code == 1 and lines["publish"].startswith("FAIL")
    env["site"].unlink()
    code, lines = _doctor(env, capsys)
    assert code == 0 and lines["publish"].startswith("WARN")


def test_groq_pass_and_fail(
    env: dict[str, Any], capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("GROQ_API_KEY", GROQ_KEY)
    env["http"].get_responses[cli.GROQ_MODELS_URL] = {"data": [{"id": "openai/gpt-oss-120b"}]}
    code, lines = _doctor(env, capsys)
    assert code == 0 and lines["groq"].startswith("PASS")
    env["http"].get_responses[cli.GROQ_MODELS_URL] = {"data": [{"id": "other"}]}
    code, lines = _doctor(env, capsys)
    assert code == 1 and "not offered" in lines["groq"]
    env["http"].get_responses[cli.GROQ_MODELS_URL] = HttpError("HTTP 401", status=401)
    code, lines = _doctor(env, capsys)
    assert code == 1 and lines["groq"].startswith("FAIL")


def test_telegram_pass_and_fail(
    env: dict[str, Any], capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", TOKEN)
    url = f"{TELEGRAM_API}/bot{TOKEN}/getMe"
    env["http"].get_responses[url] = {"ok": True, "result": {"username": "myBuddy_update_bot"}}
    code, lines = _doctor(env, capsys)
    assert code == 0 and lines["telegram"].startswith("PASS")
    env["http"].get_responses[url] = HttpError("HTTP 401", status=401)
    code, lines = _doctor(env, capsys)
    assert code == 1 and lines["telegram"].startswith("FAIL")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "not-a-token")
    code, lines = _doctor(env, capsys)
    assert code == 1 and "does not look like" in lines["telegram"]


def test_host_allowlist_fails_for_ip_webhook(
    env: dict[str, Any], capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DISCORD_WEBHOOK_URL", "https://203.0.113.9/api/webhooks/1/x")
    code, lines = _doctor(env, capsys)
    assert code == 1 and lines["host allowlist"].startswith("FAIL")


def test_disk_thresholds(env: dict[str, Any], capsys: pytest.CaptureFixture[str]) -> None:
    env["free"]["bytes"] = 500 * 1024**2
    assert _doctor(env, capsys)[1]["disk"].startswith("WARN")
    env["free"]["bytes"] = 50 * 1024**2
    code, lines = _doctor(env, capsys)
    assert code == 1 and lines["disk"].startswith("FAIL")


def test_memory_thresholds(env: dict[str, Any], capsys: pytest.CaptureFixture[str]) -> None:
    env["meminfo"].write_text("MemTotal: 1000000 kB\nMemAvailable: 100000 kB\n")
    assert _doctor(env, capsys)[1]["memory"].startswith("WARN")
    env["meminfo"].write_text("MemTotal: 1000000 kB\nMemAvailable: 10000 kB\n")
    code, lines = _doctor(env, capsys)
    assert code == 1 and lines["memory"].startswith("FAIL")
    env["meminfo"].unlink()
    assert _doctor(env, capsys)[1]["memory"].startswith("SKIP")


def test_systemd_units(env: dict[str, Any], capsys: pytest.CaptureFixture[str]) -> None:
    env["units"].update({"mybuddy.service": "active", "mybuddy-bot.service": "active"})
    code, lines = _doctor(env, capsys)
    assert code == 0
    assert lines["unit mybuddy.service"] == "PASS active"
    assert lines["unit mybuddy-publish.path"] == "WARN inactive"  # optional unit
    env["units"]["mybuddy-bot.service"] = "failed"
    code, lines = _doctor(env, capsys)
    assert code == 1 and lines["unit mybuddy-bot.service"] == "FAIL failed"
