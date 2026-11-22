"""Load and validate config/topics.yaml and runtime settings from the environment."""

from __future__ import annotations

import os
import re
from collections.abc import Collection, Mapping, MutableMapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

PACKAGE_DIR = Path(__file__).resolve().parent
REPO_ROOT = PACKAGE_DIR.parent.parent
PROMPTS_DIR = PACKAGE_DIR / "prompts"
DEFAULT_CONFIG_PATH = REPO_ROOT / "config" / "topics.yaml"
DEFAULT_ENV_FILE = Path("~/.config/mybuddy/env")

WEEKDAYS: tuple[str, ...] = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
DEFAULT_MAX_ITEMS = 10

_TOPIC_KEYS = {
    "id",
    "title",
    "emoji",
    "description",
    "schedule",
    "ai",
    "prompt",
    "rotation",
    "sources",
    "max_items",
}
_REQUIRED_TOPIC_KEYS = ("id", "title", "emoji", "description", "schedule", "ai")
_SCHEDULE_KEYS = {"time", "days", "every_minutes"}
_ROTATION_KEYS = {"title", "sources"}
_ID_RE = re.compile(r"^[a-z][a-z0-9_]{0,39}$")
_TIME_RE = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)$")


class ConfigError(Exception):
    """Raised when the topics file or a setting is invalid. Message names the location."""


@dataclass(frozen=True)
class Schedule:
    time: str | None = None  # "HH:MM" in Asia/Kolkata
    days: tuple[str, ...] = WEEKDAYS
    every_minutes: int | None = None

    @property
    def hour(self) -> int:
        return int((self.time or "00:00")[:2])

    @property
    def minute(self) -> int:
        return int((self.time or "00:00")[3:])


@dataclass(frozen=True)
class RotationEntry:
    title: str
    sources: tuple[dict[str, Any], ...]


@dataclass(frozen=True)
class Topic:
    id: str
    title: str
    emoji: str
    description: str
    schedule: Schedule
    ai: bool
    prompt: str | None = None
    sources: tuple[dict[str, Any], ...] = ()
    rotation: dict[str, RotationEntry] | None = None
    max_items: int = DEFAULT_MAX_ITEMS


@dataclass(frozen=True)
class Settings:
    groq_api_key: str | None = None
    groq_model: str = "openai/gpt-oss-120b"
    gemini_api_key: str | None = None
    gemini_model: str = "gemini-2.5-flash"
    discord_webhook_url: str | None = None
    data_dir: Path = field(default_factory=lambda: Path("~/.local/share/mybuddy").expanduser())
    site_data: Path = field(default_factory=lambda: REPO_ROOT / "site" / "data" / "reports.json")
    fake: bool = False

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> Settings:
        env = os.environ if env is None else env

        def get(name: str) -> str | None:
            value = env.get(name, "").strip()
            return value or None

        data_dir = Path(get("MYBUDDY_DATA_DIR") or "~/.local/share/mybuddy").expanduser()
        site = get("MYBUDDY_SITE_DATA")
        site_data = (
            Path(site).expanduser() if site else REPO_ROOT / "site" / "data" / "reports.json"
        )
        return cls(
            groq_api_key=get("GROQ_API_KEY"),
            groq_model=get("GROQ_MODEL") or "openai/gpt-oss-120b",
            gemini_api_key=get("GEMINI_API_KEY"),
            gemini_model=get("GEMINI_MODEL") or "gemini-2.5-flash",
            discord_webhook_url=get("DISCORD_WEBHOOK_URL"),
            data_dir=data_dir,
            site_data=site_data,
            fake=(get("MYBUDDY_FAKE") or "").lower() in {"1", "true", "yes", "on"},
        )


SETTING_ENV_VARS: tuple[tuple[str, bool], ...] = (
    # (name, is_secret)
    ("GROQ_API_KEY", True),
    ("GROQ_MODEL", False),
    ("GEMINI_API_KEY", True),
    ("GEMINI_MODEL", False),
    ("DISCORD_WEBHOOK_URL", True),
    ("MYBUDDY_DATA_DIR", False),
    ("MYBUDDY_SITE_DATA", False),
    ("MYBUDDY_FAKE", False),
)


def load_env_file(path: Path, environ: MutableMapping[str, str] | None = None) -> int:
    """Read simple KEY=VALUE lines into `environ` without overriding existing values.

    Blank lines and `#` comments are ignored; an optional `export ` prefix and matching
    surrounding quotes are stripped. Returns the number of variables set. Missing file -> 0.
    """
    environ = os.environ if environ is None else environ
    path = path.expanduser()
    if not path.is_file():
        return 0
    count = 0
    for lineno, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export ") :].lstrip()
        key, sep, value = line.partition("=")
        key = key.strip()
        if not sep or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key):
            raise ConfigError(f"{path}:{lineno}: expected KEY=VALUE")
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        if key not in environ:
            environ[key] = value
            count += 1
    return count


# --------------------------------------------------------------------------- topics


def _known_source_types() -> Collection[str] | None:
    try:
        from mybuddy.sources import SOURCE_TYPES
    except Exception:  # sources package missing or broken: skip type validation
        return None
    return set(SOURCE_TYPES)


def _require_str(value: Any, where: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ConfigError(f"{where}: must be a non-empty string")
    return value.strip()


def _check_keys(data: Mapping[str, Any], allowed: Collection[str], where: str) -> None:
    unknown = sorted(set(data) - set(allowed))
    if unknown:
        raise ConfigError(f"{where}: unknown key(s): {', '.join(map(str, unknown))}")


def _parse_schedule(data: Any, where: str) -> Schedule:
    if not isinstance(data, dict):
        raise ConfigError(f"{where}: must be a mapping")
    _check_keys(data, _SCHEDULE_KEYS, where)
    if "every_minutes" in data:
        if "time" in data or "days" in data:
            raise ConfigError(f"{where}: use either every_minutes or time/days, not both")
        every = data["every_minutes"]
        if not isinstance(every, int) or isinstance(every, bool) or not 1 <= every <= 1440:
            raise ConfigError(f"{where}.every_minutes: must be an integer between 1 and 1440")
        return Schedule(every_minutes=every)
    if "time" not in data:
        raise ConfigError(f"{where}: needs 'time' (HH:MM) or 'every_minutes'")
    time = data["time"]
    if not isinstance(time, str) or not _TIME_RE.match(time):
        raise ConfigError(f"{where}.time: must be a 24-hour 'HH:MM' string, got {time!r}")
    days_raw = data.get("days", "daily")
    if days_raw == "daily" or days_raw == ["daily"]:
        days = WEEKDAYS
    elif isinstance(days_raw, list) and days_raw:
        lowered = [str(d).strip().lower() for d in days_raw]
        bad = [d for d in lowered if d not in WEEKDAYS]
        if bad:
            raise ConfigError(f"{where}.days: unknown day(s) {bad}; use mon..sun or 'daily'")
        days = tuple(d for d in WEEKDAYS if d in lowered)
    else:
        raise ConfigError(f"{where}.days: must be 'daily' or a non-empty list of mon..sun")
    return Schedule(time=time, days=days)


def _parse_sources(
    data: Any, where: str, source_types: Collection[str] | None
) -> tuple[dict[str, Any], ...]:
    if not isinstance(data, list) or not data:
        raise ConfigError(f"{where}: must be a non-empty list")
    out: list[dict[str, Any]] = []
    names: set[str] = set()
    for i, spec in enumerate(data):
        w = f"{where}[{i}]"
        if not isinstance(spec, dict):
            raise ConfigError(f"{w}: must be a mapping")
        stype = _require_str(spec.get("type"), f"{w}.type")
        name = _require_str(spec.get("name"), f"{w}.name")
        if source_types is not None and stype not in source_types:
            known = ", ".join(sorted(source_types))
            raise ConfigError(f"{w}.type: unknown source type {stype!r} (known: {known})")
        if name in names:
            raise ConfigError(f"{w}.name: duplicate source name {name!r}")
        if any(str(k).startswith("_") for k in spec):
            raise ConfigError(f"{w}: keys starting with '_' are reserved")
        names.add(name)
        out.append(dict(spec))
    return tuple(out)


def _parse_topic(
    data: Any, where: str, prompts_dir: Path, source_types: Collection[str] | None
) -> Topic:
    if not isinstance(data, dict):
        raise ConfigError(f"{where}: must be a mapping")
    _check_keys(data, _TOPIC_KEYS, where)
    for key in _REQUIRED_TOPIC_KEYS:
        if key not in data:
            raise ConfigError(f"{where}: missing required key '{key}'")
    topic_id = _require_str(data["id"], f"{where}.id")
    if not _ID_RE.match(topic_id):
        raise ConfigError(f"{where}.id: {topic_id!r} must be lowercase letters, digits, '_'")
    where = f"{where} ({topic_id})"
    title = _require_str(data["title"], f"{where}.title")
    emoji = _require_str(data["emoji"], f"{where}.emoji")
    description = _require_str(data["description"], f"{where}.description")
    schedule = _parse_schedule(data["schedule"], f"{where}.schedule")
    ai = data["ai"]
    if not isinstance(ai, bool):
        raise ConfigError(f"{where}.ai: must be true or false")

    prompt: str | None = None
    if "prompt" in data:
        prompt = _require_str(data["prompt"], f"{where}.prompt")
        if "/" in prompt or "\\" in prompt or prompt.startswith("."):
            raise ConfigError(f"{where}.prompt: must be a plain file name")
        if not (prompts_dir / prompt).is_file():
            raise ConfigError(f"{where}.prompt: file not found: {prompts_dir / prompt}")
    elif ai:
        raise ConfigError(f"{where}: ai topics need a 'prompt' file")

    max_items = data.get("max_items", DEFAULT_MAX_ITEMS)
    if not isinstance(max_items, int) or isinstance(max_items, bool) or not 1 <= max_items <= 50:
        raise ConfigError(f"{where}.max_items: must be an integer between 1 and 50")

    sources: tuple[dict[str, Any], ...] = ()
    if "sources" in data:
        sources = _parse_sources(data["sources"], f"{where}.sources", source_types)

    rotation: dict[str, RotationEntry] | None = None
    if "rotation" in data:
        rot = data["rotation"]
        if not isinstance(rot, dict) or not rot:
            raise ConfigError(f"{where}.rotation: must be a non-empty mapping of weekday -> entry")
        rotation = {}
        for day, entry in rot.items():
            w = f"{where}.rotation.{day}"
            if str(day).lower() not in WEEKDAYS:
                raise ConfigError(f"{w}: unknown weekday; use mon..sun")
            if not isinstance(entry, dict):
                raise ConfigError(f"{w}: must be a mapping with title and sources")
            _check_keys(entry, _ROTATION_KEYS, w)
            rotation[str(day).lower()] = RotationEntry(
                title=_require_str(entry.get("title"), f"{w}.title"),
                sources=_parse_sources(entry.get("sources"), f"{w}.sources", source_types),
            )
    if not sources and not rotation:
        raise ConfigError(f"{where}: needs 'sources' or 'rotation'")

    return Topic(
        id=topic_id,
        title=title,
        emoji=emoji,
        description=description,
        schedule=schedule,
        ai=ai,
        prompt=prompt,
        sources=sources,
        rotation=rotation,
        max_items=max_items,
    )


def parse_topics(
    data: Any,
    *,
    origin: str = "topics.yaml",
    prompts_dir: Path = PROMPTS_DIR,
    source_types: Collection[str] | None = None,
) -> list[Topic]:
    if not isinstance(data, dict):
        raise ConfigError(f"{origin}: top level must be a mapping with a 'topics' list")
    _check_keys(data, {"topics"}, origin)
    raw = data.get("topics")
    if not isinstance(raw, list) or not raw:
        raise ConfigError(f"{origin}: 'topics' must be a non-empty list")
    topics: list[Topic] = []
    seen: set[str] = set()
    for i, entry in enumerate(raw):
        topic = _parse_topic(entry, f"{origin}: topics[{i}]", prompts_dir, source_types)
        if topic.id in seen:
            raise ConfigError(f"{origin}: topics[{i}]: duplicate topic id {topic.id!r}")
        seen.add(topic.id)
        topics.append(topic)
    return topics


def load_topics(
    path: Path | None = None,
    *,
    prompts_dir: Path = PROMPTS_DIR,
    source_types: Collection[str] | None = None,
    check_source_types: bool = True,
) -> list[Topic]:
    """Load and validate the topics file. Raises ConfigError with the file path on problems."""
    path = (path or DEFAULT_CONFIG_PATH).expanduser()
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ConfigError(f"{path}: cannot read config: {exc.strerror or exc}") from exc
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise ConfigError(f"{path}: invalid YAML: {exc}") from exc
    if source_types is None and check_source_types:
        source_types = _known_source_types()
    return parse_topics(data, origin=str(path), prompts_dir=prompts_dir, source_types=source_types)
