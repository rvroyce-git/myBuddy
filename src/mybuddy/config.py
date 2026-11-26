"""Load and validate config/topics.yaml and runtime settings from the environment."""

from __future__ import annotations

import os
import re
from collections.abc import Collection, Mapping, MutableMapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from mybuddy.http import host_allowlist

PACKAGE_DIR = Path(__file__).resolve().parent
REPO_ROOT = PACKAGE_DIR.parent.parent
PROMPTS_DIR = PACKAGE_DIR / "prompts"
DEFAULT_CONFIG_PATH = REPO_ROOT / "config" / "topics.yaml"
DEFAULT_ENV_FILE = Path("~/.config/mybuddy/env")

WEEKDAYS: tuple[str, ...] = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
DEFAULT_MAX_ITEMS = 10
MAX_TOPIC_BIT = 59  # payload is at most 15 hex digits = 60 bits
DEFAULT_MAX_SUBSCRIBERS = 100
MAX_SUBSCRIBERS_CEILING = 10_000
DEFAULT_BOT_USERNAME = "myBuddy_update_bot"
# Chat with the bot (owner + CHAT_ALLOWLIST only). Groq's free tier is shared with the
# scheduled reports (~200K tokens/day, 8K tokens/min), so chat gets its own small daily
# token budget, never more than MAX_CHAT_DAILY_TOKEN_BUDGET.
DEFAULT_CHAT_RATE_PER_MIN = 2
DEFAULT_CHAT_DAILY_CAP = 30
DEFAULT_CHAT_MAX_USERS_PER_DAY = 50  # distinct chatting people per IST day (public chat)
DEFAULT_CHAT_DAILY_TOKEN_BUDGET = 60_000
MAX_CHAT_DAILY_TOKEN_BUDGET = 80_000
# Public (non-owner, non-allowlisted) chats may use at most this % of the chat token budget;
# the rest is reserved for the owner and allowlist. New public chatters are capped per hour.
DEFAULT_CHAT_PUBLIC_BUDGET_SHARE = 70
DEFAULT_CHAT_NEW_USERS_PER_HOUR = 20
DEFAULT_SITE_URL = "https://rvroyce-git.github.io/myBuddy/"
_SITE_URL_RE = re.compile(r"https://[A-Za-z0-9.-]+(?:/[A-Za-z0-9._~/-]*)?")
_CHAT_ID_RE = re.compile(r"-?[0-9]{1,20}")
_BOT_USERNAME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]{3,31}$")

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
    "audience",
    "bit",
    "website",
    "kind",
    "dedupe_with",
    "enabled",
}
# news: collect from `sources`. digest/weekly: built from stored reports (no fetching).
# health: the app's own state, owner only. Only news topics can be on the website.
TOPIC_KINDS: tuple[str, ...] = ("news", "digest", "weekly", "health")
_REQUIRED_TOPIC_KEYS = ("id", "title", "emoji", "description", "schedule", "ai")
_SCHEDULE_KEYS = {"time", "days", "every_minutes"}
_ROTATION_KEYS = {"title", "sources"}
_ID_RE = re.compile(r"^[a-z][a-z0-9_]{0,39}$")
_TIME_RE = re.compile(r"^([01][0-9]|2[0-3]):([0-5][0-9])$")


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
    # Non-AI topics only: deliver this topic's reports to subscribers of that (AI) topic.
    # kev_alerts uses `audience: security`. Without it, subscribers never get the topic.
    audience: str | None = None
    # AI (website) topics only: permanent bit in Telegram start-link payloads. Never change or
    # reuse a bit, even when topics are reordered or removed.
    bit: int | None = None
    # Shown on the website (and subscribable there). None = the old rule: AI topics only.
    website: bool | None = None
    kind: str = "news"
    # News topics only: skip items another topic has already delivered (cheap URL check).
    dedupe_with: tuple[str, ...] = ()
    # `enabled: false` keeps the topic in the config (and on the website) but the scheduler
    # skips it. Owner can toggle it from the bot (/topic on|off <id>).
    enabled: bool = True

    @property
    def on_website(self) -> bool:
        return self.ai if self.website is None else self.website


@dataclass(frozen=True)
class Settings:
    # Secrets (and the owner's chat id) are left out of repr() so they never reach a log.
    groq_api_key: str | None = field(default=None, repr=False)
    groq_model: str = "openai/gpt-oss-120b"
    gemini_api_key: str | None = field(default=None, repr=False)
    gemini_model: str = "gemini-2.5-flash"
    discord_webhook_url: str | None = field(default=None, repr=False)
    telegram_bot_token: str | None = field(default=None, repr=False)
    telegram_chat_id: str | None = field(default=None, repr=False)
    data_dir: Path = field(default_factory=lambda: Path("~/.local/share/mybuddy").expanduser())
    site_data: Path = field(default_factory=lambda: REPO_ROOT / "site" / "data" / "reports.json")
    fake: bool = False
    max_subscribers: int = DEFAULT_MAX_SUBSCRIBERS
    bot_username: str = DEFAULT_BOT_USERNAME
    # Access: private by default. The owner (telegram_chat_id) is always allowed.
    public_signup: bool = False
    chat_allowlist: tuple[int, ...] = field(default=(), repr=False)  # extra chat ids
    chat_public: bool = False  # true: any private chat may use AI chat (within the limits)
    chat_max_users_per_day: int = DEFAULT_CHAT_MAX_USERS_PER_DAY
    chat_enabled: bool = True  # only takes effect with an AI key (see chat_active)
    chat_rate_per_min: int = DEFAULT_CHAT_RATE_PER_MIN
    chat_daily_cap: int = DEFAULT_CHAT_DAILY_CAP
    chat_daily_token_budget: int = DEFAULT_CHAT_DAILY_TOKEN_BUDGET
    chat_public_budget_share: int = DEFAULT_CHAT_PUBLIC_BUDGET_SHARE  # percent, 0-100
    chat_new_users_per_hour: int = DEFAULT_CHAT_NEW_USERS_PER_HOUR
    site_url: str = DEFAULT_SITE_URL  # the public website, linked from bot replies

    @property
    def chat_active(self) -> bool:
        return self.chat_enabled and bool(self.groq_api_key or self.gemini_api_key)

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
            telegram_bot_token=get("TELEGRAM_BOT_TOKEN"),
            telegram_chat_id=get("TELEGRAM_CHAT_ID"),
            data_dir=data_dir,
            site_data=site_data,
            fake=(get("MYBUDDY_FAKE") or "").lower() in {"1", "true", "yes", "on"},
            max_subscribers=_max_subscribers_or_default(get("MYBUDDY_MAX_SUBSCRIBERS")),
            bot_username=_bot_username_or_default(get("MYBUDDY_BOT_USERNAME")),
            public_signup=parse_flag(get("PUBLIC_SIGNUP"), default=False),
            chat_allowlist=parse_chat_allowlist(get("CHAT_ALLOWLIST"))[0],
            chat_public=parse_flag(get("CHAT_PUBLIC"), default=False),
            chat_max_users_per_day=parse_count(
                get("CHAT_MAX_USERS_PER_DAY"), DEFAULT_CHAT_MAX_USERS_PER_DAY, 100_000
            ),
            chat_enabled=parse_flag(get("CHAT_ENABLED"), default=bool(get("GROQ_API_KEY"))),
            chat_rate_per_min=parse_count(
                get("CHAT_RATE_PER_MIN"), DEFAULT_CHAT_RATE_PER_MIN, 1000
            ),
            chat_daily_cap=parse_count(get("CHAT_DAILY_CAP"), DEFAULT_CHAT_DAILY_CAP, 100_000),
            chat_public_budget_share=parse_count(
                get("CHAT_PUBLIC_BUDGET_SHARE"), DEFAULT_CHAT_PUBLIC_BUDGET_SHARE, 100
            ),
            chat_new_users_per_hour=parse_count(
                get("CHAT_NEW_USERS_PER_HOUR"), DEFAULT_CHAT_NEW_USERS_PER_HOUR, 100_000
            ),
            chat_daily_token_budget=parse_count(
                get("CHAT_DAILY_TOKEN_BUDGET"),
                DEFAULT_CHAT_DAILY_TOKEN_BUDGET,
                MAX_CHAT_DAILY_TOKEN_BUDGET,
            ),
            site_url=parse_site_url(get("SITE_URL")),
        )


_TRUE = {"1", "true", "yes", "on"}
_FALSE = {"0", "false", "no", "off"}


def parse_flag(raw: str | None, *, default: bool) -> bool:
    """true/false, yes/no, on/off, 1/0; anything else (or unset) means the default."""
    value = (raw or "").strip().lower()
    if value in _TRUE:
        return True
    if value in _FALSE:
        return False
    return default


def parse_count(raw: str | None, default: int, ceiling: int) -> int:
    """A whole number 0..ceiling; anything else means the default (`mybuddy check` warns)."""
    value = (raw or "").strip()
    if re.fullmatch(r"[0-9]{1,9}", value) and int(value) <= ceiling:
        return int(value)
    return default


def parse_site_url(raw: str | None) -> str:
    """SITE_URL: a plain https URL (no query, spaces or login); anything else = the default."""
    value = (raw or "").strip()
    return value if len(value) <= 200 and _SITE_URL_RE.fullmatch(value) else DEFAULT_SITE_URL


def parse_chat_allowlist(raw: str | None) -> tuple[tuple[int, ...], int]:
    """CHAT_ALLOWLIST "123, 456" -> ((123, 456), number of ignored invalid entries)."""
    ids: list[int] = []
    bad = 0
    for part in (raw or "").split(","):
        entry = part.strip()
        if not entry:
            continue
        if _CHAT_ID_RE.fullmatch(entry):
            if int(entry) not in ids:
                ids.append(int(entry))
        else:
            bad += 1
    return tuple(ids), bad


def parse_max_subscribers(raw: str | None) -> int:
    """MYBUDDY_MAX_SUBSCRIBERS: whole number 0..10000; unset means the default (100)."""
    if raw is None or not raw.strip():
        return DEFAULT_MAX_SUBSCRIBERS
    value = raw.strip()
    # ASCII digits only: str.isdigit() also accepts "²", which int() rejects.
    if not re.fullmatch(r"[0-9]{1,5}", value) or int(value) > MAX_SUBSCRIBERS_CEILING:
        raise ConfigError(
            f"MYBUDDY_MAX_SUBSCRIBERS must be a whole number 0..{MAX_SUBSCRIBERS_CEILING}, "
            f"got {value[:20]!r}"
        )
    return int(value)


def _max_subscribers_or_default(raw: str | None) -> int:
    try:
        return parse_max_subscribers(raw)
    except ConfigError:  # `mybuddy check` reports it; never crash the services over it
        return DEFAULT_MAX_SUBSCRIBERS


def _bot_username_or_default(raw: str | None) -> str:
    name = (raw or "").strip().lstrip("@")
    return name if _BOT_USERNAME_RE.fullmatch(name) else DEFAULT_BOT_USERNAME


SETTING_ENV_VARS: tuple[tuple[str, bool], ...] = (
    # (name, is_secret)
    ("GROQ_API_KEY", True),
    ("GROQ_MODEL", False),
    ("GEMINI_API_KEY", True),
    ("GEMINI_MODEL", False),
    ("DISCORD_WEBHOOK_URL", True),
    ("TELEGRAM_BOT_TOKEN", True),
    ("TELEGRAM_CHAT_ID", False),
    ("MYBUDDY_DATA_DIR", False),
    ("MYBUDDY_SITE_DATA", False),
    ("MYBUDDY_FAKE", False),
    ("MYBUDDY_MAX_SUBSCRIBERS", False),
    ("MYBUDDY_BOT_USERNAME", False),
    ("PUBLIC_SIGNUP", False),
    ("CHAT_ALLOWLIST", False),
    ("CHAT_ENABLED", False),
    ("CHAT_RATE_PER_MIN", False),
    ("CHAT_DAILY_CAP", False),
    ("CHAT_DAILY_TOKEN_BUDGET", False),
    ("CHAT_PUBLIC_BUDGET_SHARE", False),
    ("CHAT_NEW_USERS_PER_HOUR", False),
    ("SITE_URL", False),
)


def load_env_file(path: Path, environ: MutableMapping[str, str] | None = None) -> int:
    """Read simple KEY=VALUE lines into `environ` without overriding existing values.

    Blank lines and `#` comments are ignored; an optional `export ` prefix and matching
    surrounding quotes are stripped. Returns the number of variables set. Missing file -> 0.
    """
    environ = os.environ if environ is None else environ
    path = path.expanduser()
    try:
        if not path.is_file():
            return 0
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise ConfigError(f"{path}: cannot read env file: {_reason(exc)}") from None
    count = 0
    for lineno, raw in enumerate(text.splitlines(), start=1):
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


def _reason(exc: Exception) -> str:
    strerror = getattr(exc, "strerror", None)
    return str(strerror or exc)


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
    if not isinstance(time, str) or not _TIME_RE.fullmatch(time):
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
    if not _ID_RE.fullmatch(topic_id):
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

    kind = data.get("kind", "news")
    if kind not in TOPIC_KINDS:
        raise ConfigError(f"{where}.kind: must be one of {', '.join(TOPIC_KINDS)}")

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
    if kind == "news" and not sources and not rotation:
        raise ConfigError(f"{where}: needs 'sources' or 'rotation'")
    if kind != "news" and (sources or rotation):
        raise ConfigError(f"{where}: {kind} topics use stored state; remove sources/rotation")

    website = data.get("website", ai)
    if not isinstance(website, bool):
        raise ConfigError(f"{where}.website: must be true or false")
    if website and not ai:
        raise ConfigError(f"{where}.website: only AI topics can be shown on the website")

    bit: int | None = None
    if website:
        if "bit" not in data:
            raise ConfigError(
                f"{where}: website topics need a permanent 'bit' (0..{MAX_TOPIC_BIT})"
            )
        bit = data["bit"]
        if not isinstance(bit, int) or isinstance(bit, bool) or not 0 <= bit <= MAX_TOPIC_BIT:
            raise ConfigError(f"{where}.bit: must be an integer between 0 and {MAX_TOPIC_BIT}")
    elif "bit" in data:
        raise ConfigError(f"{where}.bit: only AI website topics have a subscription bit")

    if kind != "news" and website:
        raise ConfigError(f"{where}: {kind} topics are Telegram-only; set website: false")
    if kind in ("digest", "weekly") and not ai:
        raise ConfigError(f"{where}: {kind} topics need ai: true and a prompt")
    if kind == "health" and ai:
        raise ConfigError(f"{where}: health topics do not use AI; set ai: false")

    audience: str | None = None
    if "audience" in data:
        audience = _require_str(data["audience"], f"{where}.audience")
        if website:
            raise ConfigError(f"{where}.audience: only non-website topics can borrow an audience")
        if kind == "health":
            raise ConfigError(f"{where}.audience: health reports go to the owner only")

    dedupe_with: tuple[str, ...] = ()
    if "dedupe_with" in data:
        raw_dedupe = data["dedupe_with"]
        if not isinstance(raw_dedupe, list) or not all(
            isinstance(d, str) and d.strip() for d in raw_dedupe
        ):
            raise ConfigError(f"{where}.dedupe_with: must be a list of topic ids")
        if kind != "news":
            raise ConfigError(f"{where}.dedupe_with: only news topics collect items")
        dedupe_with = tuple(d.strip() for d in raw_dedupe)

    enabled = data.get("enabled", True)
    if not isinstance(enabled, bool):
        raise ConfigError(f"{where}.enabled: must be true or false")

    return Topic(
        enabled=enabled,
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
        audience=audience,
        bit=bit,
        website=website,
        kind=kind,
        dedupe_with=dedupe_with,
    )


@dataclass(frozen=True)
class Config:
    topics: list[Topic]
    # Hosts that configured URLs are known to redirect to (exact names, no IPs or wildcards).
    extra_allowed_hosts: frozenset[str] = frozenset()


def _parse_extra_hosts(data: Any, where: str) -> frozenset[str]:
    if data is None:
        return frozenset()
    if not isinstance(data, list):
        raise ConfigError(f"{where}: must be a list of host names")
    try:
        return host_allowlist(data)
    except ValueError as exc:
        raise ConfigError(f"{where}: {exc}") from None


def parse_config(
    data: Any,
    *,
    origin: str = "topics.yaml",
    prompts_dir: Path = PROMPTS_DIR,
    source_types: Collection[str] | None = None,
) -> Config:
    topics = parse_topics(data, origin=origin, prompts_dir=prompts_dir, source_types=source_types)
    extra = _parse_extra_hosts(data.get("extra_allowed_hosts"), f"{origin}: extra_allowed_hosts")
    return Config(topics=topics, extra_allowed_hosts=extra)


def parse_topics(
    data: Any,
    *,
    origin: str = "topics.yaml",
    prompts_dir: Path = PROMPTS_DIR,
    source_types: Collection[str] | None = None,
) -> list[Topic]:
    if not isinstance(data, dict):
        raise ConfigError(f"{origin}: top level must be a mapping with a 'topics' list")
    _check_keys(data, {"topics", "extra_allowed_hosts"}, origin)
    raw = data.get("topics")
    if not isinstance(raw, list) or not raw:
        raise ConfigError(f"{origin}: 'topics' must be a non-empty list")
    topics: list[Topic] = []
    seen: set[str] = set()
    bits: dict[int, str] = {}
    for i, entry in enumerate(raw):
        topic = _parse_topic(entry, f"{origin}: topics[{i}]", prompts_dir, source_types)
        if topic.id in seen:
            raise ConfigError(f"{origin}: topics[{i}]: duplicate topic id {topic.id!r}")
        if topic.bit is not None:
            if topic.bit in bits:
                raise ConfigError(
                    f"{origin}: topics[{i}] ({topic.id}).bit: {topic.bit} is already used by "
                    f"{bits[topic.bit]!r}"
                )
            bits[topic.bit] = topic.id
        seen.add(topic.id)
        topics.append(topic)
    website_ids = {t.id for t in topics if t.on_website}
    for i, topic in enumerate(topics):
        if topic.audience is not None and topic.audience not in website_ids:
            raise ConfigError(
                f"{origin}: topics[{i}] ({topic.id}).audience: {topic.audience!r} must be the "
                "id of a website topic in this file"
            )
        for other in topic.dedupe_with:
            if other not in seen or other == topic.id:
                raise ConfigError(
                    f"{origin}: topics[{i}] ({topic.id}).dedupe_with: {other!r} must be the "
                    "id of another topic in this file"
                )
    return topics


def validate_config_text(text: str, *, origin: str = "topics.yaml") -> Config:
    """Parse and validate topics.yaml text exactly as load_config would. Raises ConfigError."""
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise ConfigError(f"{origin}: invalid YAML: {exc}") from exc
    return parse_config(data, origin=origin, source_types=_known_source_types())


def load_topics(
    path: Path | None = None,
    *,
    prompts_dir: Path = PROMPTS_DIR,
    source_types: Collection[str] | None = None,
    check_source_types: bool = True,
) -> list[Topic]:
    """Load and validate the topics file. Raises ConfigError with the file path on problems."""
    return load_config(
        path,
        prompts_dir=prompts_dir,
        source_types=source_types,
        check_source_types=check_source_types,
    ).topics


def load_config(
    path: Path | None = None,
    *,
    prompts_dir: Path = PROMPTS_DIR,
    source_types: Collection[str] | None = None,
    check_source_types: bool = True,
) -> Config:
    """Load and validate the topics file and its other settings (extra_allowed_hosts)."""
    path = (path or DEFAULT_CONFIG_PATH).expanduser()
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise ConfigError(f"{path}: cannot read config: {_reason(exc)}") from exc
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise ConfigError(f"{path}: invalid YAML: {exc}") from exc
    if source_types is None and check_source_types:
        source_types = _known_source_types()
    return parse_config(data, origin=str(path), prompts_dir=prompts_dir, source_types=source_types)
