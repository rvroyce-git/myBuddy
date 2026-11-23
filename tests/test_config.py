from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import pytest
import yaml

from mybuddy.config import (
    DEFAULT_CONFIG_PATH,
    WEEKDAYS,
    ConfigError,
    Settings,
    load_env_file,
    load_topics,
    parse_topics,
)


def _topic(**kw: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "id": "ai_news",
        "title": "AI news",
        "emoji": "🧠",
        "description": "d",
        "schedule": {"time": "06:30", "days": "daily"},
        "ai": True,
        "bit": 0,
        "prompt": "ai_news.md",
        "sources": [{"type": "rss", "name": "A", "url": "https://a.example/feed"}],
    }
    base.update(kw)
    return base


def _parse(*topics: dict[str, Any]) -> Any:
    return parse_topics({"topics": list(topics)}, source_types={"rss", "arxiv", "cisa_kev"})


def test_shipped_config_is_valid() -> None:
    topics = load_topics(DEFAULT_CONFIG_PATH)
    assert [t.id for t in topics] == [
        "ai_news",
        "security",
        "kev_alerts",
        "github_trending",
        "deep_dive",
        "research_papers",
        "startups",
        "ai_models",
    ]
    for t in topics:
        assert t.emoji and t.description
        assert len(t.sources) <= 4
        for entry in (t.rotation or {}).values():
            assert len(entry.sources) <= 4
    deep = next(t for t in topics if t.id == "deep_dive")
    assert deep.rotation is not None and set(deep.rotation) == set(WEEKDAYS[:5])
    kev = next(t for t in topics if t.id == "kev_alerts")
    assert kev.ai is False and kev.schedule.every_minutes == 60


def test_good_config_parses() -> None:
    [t] = _parse(_topic(schedule={"time": "07:30", "days": ["fri", "mon"]}))
    assert t.schedule.days == ("mon", "fri")
    assert t.schedule.hour == 7 and t.schedule.minute == 30


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"colour": "red"}, "unknown key"),
        ({"id": "Bad Id"}, "lowercase"),
        ({"schedule": {"time": "25:00"}}, "HH:MM"),
        ({"schedule": {"time": "06:30", "days": ["funday"]}}, "unknown day"),
        ({"schedule": {"every_minutes": 0}}, "every_minutes"),
        ({"schedule": {"every_minutes": 5, "time": "06:30"}}, "not both"),
        ({"schedule": {"days": "daily"}}, "needs 'time'"),
        ({"prompt": "missing.md"}, "file not found"),
        ({"prompt": "../etc/passwd"}, "plain file name"),
        ({"ai": "yes"}, "true or false"),
        ({"sources": []}, "non-empty list"),
        ({"sources": [{"type": "nope", "name": "X"}]}, "unknown source type"),
        ({"sources": [{"type": "rss"}]}, "name"),
        ({"sources": [{"type": "rss", "name": "A"}, {"type": "rss", "name": "A"}]}, "duplicate"),
        ({"sources": [{"type": "rss", "name": "A", "_state": {}}]}, "reserved"),
        ({"rotation": {"someday": {"title": "x", "sources": []}}}, "unknown weekday"),
        ({"max_items": 0}, "max_items"),
    ],
)
def test_bad_configs_rejected(change: dict[str, Any], message: str) -> None:
    with pytest.raises(ConfigError, match=message):
        _parse(_topic(**change))


def test_missing_required_key() -> None:
    t = _topic()
    del t["emoji"]
    with pytest.raises(ConfigError, match="missing required key 'emoji'"):
        _parse(t)


def test_ai_topic_needs_prompt() -> None:
    t = _topic()
    del t["prompt"]
    with pytest.raises(ConfigError, match="need a 'prompt'"):
        _parse(t)


def test_duplicate_ids_rejected() -> None:
    with pytest.raises(ConfigError, match="duplicate topic id"):
        _parse(_topic(), _topic())


def test_topic_needs_sources_or_rotation() -> None:
    t = _topic()
    del t["sources"]
    with pytest.raises(ConfigError, match="needs 'sources' or 'rotation'"):
        _parse(t)


def test_unknown_top_level_key() -> None:
    with pytest.raises(ConfigError, match="unknown key"):
        parse_topics({"topics": [_topic()], "extra": 1})


def test_error_mentions_file_path(tmp_path: Path) -> None:
    path = tmp_path / "topics.yaml"
    path.write_text(yaml.safe_dump({"topics": [_topic(id="Bad")]}), encoding="utf-8")
    with pytest.raises(ConfigError) as err:
        load_topics(path, source_types={"rss"})
    assert str(path) in str(err.value) and "topics[0]" in str(err.value)


def test_invalid_yaml_and_missing_file(tmp_path: Path) -> None:
    bad = tmp_path / "bad.yaml"
    bad.write_text("topics: [", encoding="utf-8")
    with pytest.raises(ConfigError, match="invalid YAML"):
        load_topics(bad)
    with pytest.raises(ConfigError, match="cannot read"):
        load_topics(tmp_path / "nope.yaml")


def test_settings_from_env_defaults_and_values(tmp_path: Path) -> None:
    s = Settings.from_env({})
    assert s.groq_model == "openai/gpt-oss-120b"
    assert s.gemini_model == "gemini-2.5-flash"
    assert s.data_dir == Path("~/.local/share/mybuddy").expanduser()
    assert s.site_data.name == "reports.json" and s.fake is False
    s = Settings.from_env(
        {
            "GROQ_API_KEY": "k",
            "GROQ_MODEL": "m",
            "MYBUDDY_DATA_DIR": str(tmp_path),
            "MYBUDDY_SITE_DATA": str(tmp_path / "r.json"),
            "MYBUDDY_FAKE": "1",
            "DISCORD_WEBHOOK_URL": "https://discord.com/api/webhooks/x",
        }
    )
    assert (s.groq_api_key, s.groq_model, s.data_dir, s.fake) == ("k", "m", tmp_path, True)
    assert s.site_data == tmp_path / "r.json"


def test_env_file_does_not_override(tmp_path: Path) -> None:
    f = tmp_path / "env"
    f.write_text(
        "# comment\n\nexport GROQ_API_KEY='from-file'\nGROQ_MODEL=\"file-model\"\n",
        encoding="utf-8",
    )
    env = {"GROQ_API_KEY": "real"}
    assert load_env_file(f, env) == 1
    assert env == {"GROQ_API_KEY": "real", "GROQ_MODEL": "file-model"}
    assert load_env_file(tmp_path / "missing", env) == 0
    f.write_text("not a pair\n", encoding="utf-8")
    with pytest.raises(ConfigError, match=":1:"):
        load_env_file(f, env)
    assert "GROQ_API_KEY" not in os.environ or os.environ["GROQ_API_KEY"] != "from-file"
