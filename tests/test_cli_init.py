"""mybuddy init: plan first, nothing written without an exact "yes", answers file mode, and
the live config (MYBUDDY_CONFIG, else ~/.config/mybuddy/config/topics.yaml)."""

from __future__ import annotations

import stat
from pathlib import Path
from typing import Any

import pytest
import yaml

from mybuddy import cli
from mybuddy.config import DEFAULT_CONFIG_PATH, load_config

SECRET = "gsk_init_test_secret_value"
BASE_IDS = [t.id for t in load_config(DEFAULT_CONFIG_PATH).topics]


@pytest.fixture
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    for name, _ in cli.SETTING_ENV_VARS:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.delenv("MYBUDDY_CONFIG", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("MYBUDDY_DATA_DIR", str(tmp_path / "data"))
    (tmp_path / "data").mkdir(mode=0o700)
    envfile = tmp_path / "env"
    envfile.write_text(f"GROQ_API_KEY={SECRET}\n", encoding="utf-8")
    envfile.chmod(0o600)
    out = tmp_path / "live" / "topics.yaml"
    return {
        "tmp": tmp_path,
        "envfile": envfile,
        "out": out,
        "argv": ["--env-file", str(envfile)],
        "init": ["init", "--output", str(out)],
    }


def _replies(monkeypatch: pytest.MonkeyPatch, *answers: str) -> list[str]:
    """Feed input() these lines, then EOF. Returns the prompts that were shown."""
    queue = list(answers)
    prompts: list[str] = []

    def fake_input(prompt: str = "") -> str:
        prompts.append(prompt)
        if not queue:
            raise EOFError
        return queue.pop(0)

    monkeypatch.setattr("builtins.input", fake_input)
    return prompts


def _answers(env: dict[str, Any], data: dict[str, Any]) -> Path:
    path = env["tmp"] / "answers.yaml"
    path.write_text(yaml.safe_dump(data), encoding="utf-8")
    return path


def _enabled(path: Path) -> list[str]:
    return [t.id for t in load_config(path).topics if t.enabled]


def test_interactive_shows_plan_and_writes_nothing_without_yes(
    env: dict[str, Any], monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    prompts = _replies(monkeypatch, "1, security", "telegram", "ai_news=06:45", "no")
    assert cli.main([*env["argv"], *env["init"]]) == 1
    out = capsys.readouterr().out
    assert "MyBuddy setup plan" in out and "Asia/Kolkata (IST)" in out
    assert "ai_news" in out and "Daily · 06:45 IST" in out and "openai.com" in out
    assert f"write   {env['out']} (mode 600, folder 700)" in out
    assert "Turned off (kept in the file as enabled: false): " in out
    assert "TELEGRAM_BOT_TOKEN   missing" in out and "GROQ_API_KEY         set" in out
    assert "deploy/set-secrets.sh" in out and "mybuddy doctor" in out
    assert SECRET not in out
    assert "Cancelled; nothing was written." in out
    assert prompts[-1].startswith('Type "yes"')
    assert not env["out"].exists() and not env["out"].parent.exists()
    assert env["envfile"].read_text(encoding="utf-8") == f"GROQ_API_KEY={SECRET}\n"


@pytest.mark.parametrize("reply", ["YES", "y", "yes please", " yes", ""])
def test_only_exact_yes_writes(
    env: dict[str, Any], monkeypatch: pytest.MonkeyPatch, reply: str
) -> None:
    _replies(monkeypatch, "all", "", "", reply)
    assert cli.main([*env["argv"], *env["init"]]) == 1
    assert not env["out"].exists()


def test_yes_writes_private_valid_config_and_env_line(
    env: dict[str, Any], monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _replies(monkeypatch, "ai_news security kev_alerts", "outbox", "security=07:10", "yes")
    assert cli.main([*env["argv"], *env["init"]]) == 0
    assert SECRET not in capsys.readouterr().out
    assert stat.S_IMODE(env["out"].stat().st_mode) == 0o600
    assert stat.S_IMODE(env["out"].parent.stat().st_mode) == 0o700
    topics = load_config(env["out"]).topics
    assert [t.id for t in topics] == BASE_IDS  # nothing removed: the rest is turned off
    assert _enabled(env["out"]) == ["ai_news", "security", "kev_alerts"]
    assert next(t for t in topics if t.id == "security").schedule.time == "07:10"
    text = env["envfile"].read_text(encoding="utf-8")
    assert text == f"GROQ_API_KEY={SECRET}\nMYBUDDY_CONFIG={env['out']}\n"
    assert stat.S_IMODE(env["envfile"].stat().st_mode) == 0o600


def test_default_output_is_the_live_config(
    env: dict[str, Any], capsys: pytest.CaptureFixture[str]
) -> None:
    answers = _answers(env, {"topics": ["ai_news"]})
    assert cli.main([*env["argv"], "init", "--answers", str(answers), "--yes"]) == 0
    live = env["tmp"] / "home" / ".config" / "mybuddy" / "config" / "topics.yaml"
    assert _enabled(live) == ["ai_news"]
    assert stat.S_IMODE(live.stat().st_mode) == 0o600
    assert stat.S_IMODE(live.parent.stat().st_mode) == 0o700
    # Every parent dir init created is private too, not left at the umask default (775).
    for folder in (live.parent.parent, live.parent.parent.parent):
        assert stat.S_IMODE(folder.stat().st_mode) == 0o700
    assert f"MYBUDDY_CONFIG={live}" in env["envfile"].read_text(encoding="utf-8")


def test_mybuddy_config_is_used_and_env_file_left_alone(
    env: dict[str, Any], monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    target = env["tmp"] / "server" / "topics.yaml"
    env["envfile"].write_text(f"GROQ_API_KEY={SECRET}\nMYBUDDY_CONFIG={target}\n")
    answers = _answers(env, {"topics": ["security", "kev_alerts"]})
    before = env["envfile"].read_text(encoding="utf-8")
    assert cli.main([*env["argv"], "init", "--answers", str(answers), "--yes"]) == 0
    assert "MYBUDDY_CONFIG" not in capsys.readouterr().out.split("Files:")[1].split("Secrets")[0]
    assert _enabled(target) == ["security", "kev_alerts"]
    assert env["envfile"].read_text(encoding="utf-8") == before


def test_rerun_keeps_the_live_file_and_turns_topics_back_on(
    env: dict[str, Any], capsys: pytest.CaptureFixture[str]
) -> None:
    argv = [*env["argv"], *env["init"], "--answers"]
    assert cli.main([*argv, str(_answers(env, {"topics": ["ai_news"]})), "--yes"]) == 0
    # The bot (or the owner) changes the live file afterwards: init must keep that.
    data = yaml.safe_load(env["out"].read_text(encoding="utf-8"))
    data["topics"][0]["sources"].append(
        {"type": "rss", "name": "Extra", "url": "https://x.example/f"}
    )
    env["out"].write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")
    (env["tmp"] / "data" / "reload-config").unlink(missing_ok=True)
    assert cli.main([*argv, str(_answers(env, {"topics": "all"})), "--yes", "--force"]) == 0
    assert _enabled(env["out"]) == BASE_IDS
    names = [s["name"] for s in load_config(env["out"]).topics[0].sources]
    assert "Extra" in names
    backups = list((env["out"].parent / "backups").iterdir())
    assert len(backups) == 1 and stat.S_IMODE(backups[0].stat().st_mode) == 0o600
    assert (env["tmp"] / "data" / "reload-config").exists()  # the scheduler reloads


def test_input_ending_cancels(env: dict[str, Any], monkeypatch: pytest.MonkeyPatch) -> None:
    _replies(monkeypatch, "all")
    assert cli.main([*env["argv"], *env["init"]]) == 1
    assert not env["out"].exists()


def test_bad_answer_is_asked_again(
    env: dict[str, Any], monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _replies(monkeypatch, "nope", "ai_news", "pigeon", "telegram", "ai_news=25:00", "", "yes")
    assert cli.main([*env["argv"], *env["init"]]) == 0
    out = capsys.readouterr().out
    assert "unknown topic 'nope'" in out and "delivery: choose one of" in out
    assert _enabled(env["out"]) == ["ai_news"]


def test_answers_file_with_yes(env: dict[str, Any], capsys: pytest.CaptureFixture[str]) -> None:
    answers = _answers(env, {"topics": "all", "delivery": "discord"})
    argv = [*env["argv"], *env["init"], "--answers", str(answers), "--yes"]
    assert cli.main(argv) == 0
    out = capsys.readouterr().out
    assert "DISCORD_WEBHOOK_URL" in out and "TELEGRAM_BOT_TOKEN" not in out
    assert _enabled(env["out"]) == BASE_IDS
    # Idempotent: same answers again change nothing.
    before = env["out"].stat().st_mtime_ns
    assert cli.main(argv) == 0
    assert "Already set up; nothing to write." in capsys.readouterr().out
    assert env["out"].stat().st_mtime_ns == before


def test_answers_file_without_yes_still_asks(
    env: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    prompts = _replies(monkeypatch, "no")
    answers = _answers(env, {"topics": ["ai_news"]})
    assert cli.main([*env["argv"], *env["init"], "--answers", str(answers)]) == 1
    assert len(prompts) == 1 and not env["out"].exists()


def test_refuses_to_overwrite_without_force(
    env: dict[str, Any], capsys: pytest.CaptureFixture[str]
) -> None:
    env["out"].parent.mkdir()
    env["out"].write_text(DEFAULT_CONFIG_PATH.read_text(encoding="utf-8"), encoding="utf-8")
    original = env["out"].read_text(encoding="utf-8")
    answers = _answers(env, {"topics": ["ai_news"]})
    argv = [*env["argv"], *env["init"], "--answers", str(answers), "--yes"]
    assert cli.main(argv) == 1
    assert "OVERWRITE" in capsys.readouterr().out
    assert env["out"].read_text(encoding="utf-8") == original
    assert "MYBUDDY_CONFIG" not in env["envfile"].read_text(encoding="utf-8")
    assert cli.main([*argv, "--force"]) == 0
    assert _enabled(env["out"]) == ["ai_news"]


def test_broken_existing_file_is_not_overwritten(
    env: dict[str, Any], capsys: pytest.CaptureFixture[str]
) -> None:
    env["out"].parent.mkdir()
    env["out"].write_text("topics: []\n", encoding="utf-8")
    answers = _answers(env, {"topics": "all"})
    argv = [*env["argv"], *env["init"], "--answers", str(answers), "--yes", "--force"]
    assert cli.main(argv) == 2
    assert "base topics file" in capsys.readouterr().err
    assert env["out"].read_text(encoding="utf-8") == "topics: []\n"
    # --from starts over from the repo defaults instead.
    assert cli.main([*argv, "--from", str(DEFAULT_CONFIG_PATH)]) == 0
    assert _enabled(env["out"]) == BASE_IDS


@pytest.mark.parametrize(
    ("data", "message"),
    [
        ({"topics": ["ghost"]}, "unknown topic 'ghost'"),
        ({"topics": "all", "delivery": "email"}, "delivery: choose one of"),
        ({"topics": "all", "schedule": {"ai_news": "6:30"}}, "time must be 'HH:MM'"),
        ({"topics": "all", "schedule": {"kev_alerts": "06:30"}}, "runs on an interval"),
        ({"topics": ["ai_news"], "schedule": {"security": "06:30"}}, "not one of the enabled"),
        ({"topics": "all", "GROQ_API_KEY": "x"}, "unknown key(s): GROQ_API_KEY"),
        ({"topics": {"ai_news": 1}}, "use 'all' or a list"),
    ],
)
def test_validation_errors_write_nothing(
    env: dict[str, Any], capsys: pytest.CaptureFixture[str], data: dict[str, Any], message: str
) -> None:
    answers = _answers(env, data)
    assert cli.main([*env["argv"], *env["init"], "--answers", str(answers), "--yes"]) == 2
    err = capsys.readouterr().err
    assert message in err and "nothing was written" in err
    assert not env["out"].exists()
    assert "MYBUDDY_CONFIG" not in env["envfile"].read_text(encoding="utf-8")


def test_invalid_result_is_refused(
    env: dict[str, Any], capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    from mybuddy.cli import init  # noqa: PLC0415

    def bad(base: dict[str, Any], answers: Any) -> dict[str, Any]:
        return {"topics": []}

    monkeypatch.setattr(init, "build_config", bad)
    answers = _answers(env, {"topics": "all"})
    assert cli.main([*env["argv"], *env["init"], "--answers", str(answers), "--yes"]) == 2
    assert "would be invalid" in capsys.readouterr().err
    assert not env["out"].exists()


def test_yes_needs_answers(env: dict[str, Any]) -> None:
    assert cli.main([*env["argv"], *env["init"], "--yes"]) == 2
    assert not env["out"].exists()
