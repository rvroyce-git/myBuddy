"""mybuddy init: guided setup that shows the complete plan and writes nothing until "yes".

Interactive by default; `--answers FILE --yes` runs without prompts. It asks which topics to
enable, the delivery channel and optional time changes (the timezone is always IST),
validates the result with the normal config loader and writes the live config: the
MYBUDDY_CONFIG path when set, else ~/.config/mybuddy/config/topics.yaml (the same file the
services and the bot's approved /set changes use; mode 0600, folder 0700). Topics that are not
chosen get `enabled: false` (kept in the file, and on the website), so nothing is lost and a
later run can switch them back on. Overwriting keeps a backup in `backups/` (like the bot's
changes, so /undo can restore it) and asks the running scheduler to reload.

It never asks for, reads out or prints secret values: secrets are set with
deploy/set-secrets.sh. Without MYBUDDY_CONFIG it adds that one line to the env file.
"""

from __future__ import annotations

import copy
import os
import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml

from mybuddy.allowlist import source_hosts, topic_source_specs
from mybuddy.cli._common import _err, make_private_dirs, write_private
from mybuddy.config import (
    DEFAULT_CONFIG_PATH,
    ConfigError,
    Settings,
    Topic,
    load_config,
    parse_config,
)
from mybuddy.configedit import BACKUP_DIR, RELOAD_MARKER
from mybuddy.deliver import TELEGRAM_API
from mybuddy.export import schedule_label
from mybuddy.http import url_host
from mybuddy.schedule import IST
from mybuddy.store import ensure_private_dir
from mybuddy.summarize import GROQ_URL

LIVE_CONFIG_PATH = Path("~/.config/mybuddy/config/topics.yaml")  # as seeded by deploy.sh
CHANNELS = ("telegram", "discord", "outbox")
ANSWER_KEYS = {"topics", "delivery", "schedule"}
CHANNEL_ENV = {
    "telegram": ("TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID"),
    "discord": ("DISCORD_WEBHOOK_URL",),
    "outbox": (),
}
_TIME_RE = re.compile(r"^([01][0-9]|2[0-3]):[0-5][0-9]$")
_CONFIG_LINE = re.compile(r"\s*(?:export\s+)?MYBUDDY_CONFIG\s*=")
MAX_TRIES = 3


class InitError(Exception):
    """A bad answer. The message is safe to print."""


class Abort(Exception):
    """The user cancelled (or input ended)."""


@dataclass
class Answers:
    topics: list[str]
    delivery: str
    schedule: dict[str, str] = field(default_factory=dict)


def default_output() -> Path:
    """MYBUDDY_CONFIG (environment or env file) when set, else the live config path."""
    env = os.environ.get("MYBUDDY_CONFIG", "").strip()
    return Path(env).expanduser() if env else LIVE_CONFIG_PATH.expanduser()


# ---------------------------------------------------------------------- answers


def parse_topic_choice(raw: Any, available: Sequence[str], current: Sequence[str]) -> list[str]:
    """Blank keeps `current`; 'all'; or ids / 1-based numbers (commas or spaces, or a list)."""
    if raw is None or (isinstance(raw, str) and not raw.strip()):
        return [t for t in available if t in current]
    if isinstance(raw, str) and raw.strip().lower() == "all":
        return list(available)
    if isinstance(raw, str):
        tokens = [t for t in re.split(r"[,\s]+", raw.strip()) if t]
    elif isinstance(raw, list) and all(isinstance(t, str | int) for t in raw):
        tokens = [str(t) for t in raw]
    else:
        raise InitError("topics: use 'all' or a list of topic ids")
    chosen: list[str] = []
    for token in tokens:
        if token.isdigit() and 1 <= int(token) <= len(available):
            token = available[int(token) - 1]
        if token not in available:
            raise InitError(f"unknown topic {token[:40]!r}; choose from {', '.join(available)}")
        if token not in chosen:
            chosen.append(token)
    if not chosen:
        raise InitError("pick at least one topic")
    return [t for t in available if t in chosen]  # keep the file's order


def parse_delivery(raw: Any) -> str:
    value = (raw if isinstance(raw, str) else "telegram" if raw is None else "").strip().lower()
    value = value or "telegram"
    if value not in CHANNELS:
        raise InitError(f"delivery: choose one of {', '.join(CHANNELS)}")
    return value


def parse_schedule(raw: Any) -> dict[str, str]:
    """{topic: "HH:MM"} from a mapping, or "id=HH:MM, id=HH:MM" text."""
    if raw is None or raw == "" or raw == {}:
        return {}
    if isinstance(raw, str):
        out: dict[str, str] = {}
        for part in [p for p in re.split(r"[,\s]+", raw.strip()) if p]:
            key, sep, value = part.partition("=")
            if not sep:
                raise InitError(f"schedule: expected id=HH:MM, got {part[:40]!r}")
            out[key.strip()] = value.strip()
        raw = out
    if not isinstance(raw, dict):
        raise InitError("schedule: must map topic ids to 'HH:MM' times")
    result: dict[str, str] = {}
    for key, value in raw.items():
        if not isinstance(value, str) or not _TIME_RE.fullmatch(value):
            raise InitError(f"schedule.{str(key)[:40]}: time must be 'HH:MM' (24-hour, IST)")
        result[str(key)] = value
    return result


def load_answers(path: Path, topics: Sequence[Topic]) -> Answers:
    try:
        data = yaml.safe_load(path.expanduser().read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError) as exc:
        raise InitError(f"{path}: cannot read answers: {exc}") from None
    except yaml.YAMLError as exc:
        raise InitError(f"{path}: invalid YAML: {exc}") from None
    if not isinstance(data, dict):
        raise InitError(f"{path}: must be a mapping with topics, delivery, schedule")
    unknown = sorted(str(k) for k in data if k not in ANSWER_KEYS)
    if unknown:
        # Secrets never belong here: they are set with deploy/set-secrets.sh.
        raise InitError(
            f"{path}: unknown key(s): {', '.join(unknown)} (allowed: topics, delivery, "
            "schedule; secrets go in deploy/set-secrets.sh)"
        )
    ids = [t.id for t in topics]
    return Answers(
        topics=parse_topic_choice(data.get("topics"), ids, [t.id for t in topics if t.enabled]),
        delivery=parse_delivery(data.get("delivery")),
        schedule=parse_schedule(data.get("schedule")),
    )


def _ask(prompt: str) -> str:
    try:
        return input(prompt)
    except EOFError:
        raise Abort() from None


def _ask_until_valid(prompt: str, parse: Any) -> Any:
    for attempt in range(MAX_TRIES):
        try:
            return parse(_ask(prompt))
        except InitError as exc:
            print(f"  {exc}")
            if attempt == MAX_TRIES - 1:
                raise
    raise AssertionError("unreachable")  # pragma: no cover


def ask_answers(topics: Sequence[Topic]) -> Answers:
    ids = [t.id for t in topics]
    current = [t.id for t in topics if t.enabled]
    print("Topics you can enable:")
    for n, t in enumerate(topics, start=1):
        state = "on " if t.enabled else "off"
        print(f"  {n:>2}. [{state}] {t.emoji} {t.id:<18} {t.title} ({schedule_label(t.schedule)})")
    chosen = _ask_until_valid(
        "Topics to enable (ids or numbers, comma separated; 'all'; Enter keeps the [on] ones): ",
        lambda raw: parse_topic_choice(raw, ids, current),
    )
    delivery = _ask_until_valid(
        "Delivery channel (telegram / discord / outbox) [telegram]: ", parse_delivery
    )
    print("Timezone: Asia/Kolkata (IST), fixed. Times below are IST.")
    schedule = _ask_until_valid(
        "Change any times? id=HH:MM, comma separated (Enter keeps them): ", parse_schedule
    )
    return Answers(chosen, delivery, schedule)


# ---------------------------------------------------------------------- plan


def build_config(base: dict[str, Any], answers: Answers) -> dict[str, Any]:
    data = copy.deepcopy(base)
    entries = [t for t in data.get("topics", []) if isinstance(t, dict)]
    by_id = {t.get("id"): t for t in entries}
    for topic_id, time in answers.schedule.items():
        if topic_id not in answers.topics:
            raise InitError(f"schedule.{topic_id[:40]}: not one of the enabled topics")
        sched = by_id[topic_id].get("schedule")
        if not isinstance(sched, dict) or "time" not in sched:
            raise InitError(f"schedule.{topic_id}: this topic runs on an interval, not a time")
        by_id[topic_id]["schedule"] = {**sched, "time": time}
    for entry in entries:
        if entry.get("id") in answers.topics:
            entry.pop("enabled", None)  # enabled is the default
        else:
            entry["enabled"] = False
    return data


def render(data: dict[str, Any], answers: Answers) -> str:
    header = (
        "# MyBuddy topics, written by `mybuddy init` (re-run it, or use /set in the bot).\n"
        f"# Delivery channel: {answers.delivery}. Times are Asia/Kolkata (IST).\n"
        "# Check with: mybuddy doctor\n"
    )
    body = yaml.safe_dump(data, sort_keys=False, allow_unicode=True, width=100)
    return header + body


@dataclass
class Plan:
    output: Path
    text: str
    topics: list[Topic]
    extra_hosts: frozenset[str]
    answers: Answers
    file_state: str  # new | overwrite | unchanged
    env_file: Path
    env_update: bool
    env_points_elsewhere: str | None

    @property
    def enabled(self) -> list[Topic]:
        return [t for t in self.topics if t.enabled]

    @property
    def nothing_to_do(self) -> bool:
        return self.file_state == "unchanged" and not self.env_update


def make_plan(base: dict[str, Any], answers: Answers, output: Path, env_file: Path) -> Plan:
    data = build_config(base, answers)
    try:
        config = parse_config(data, origin=str(output))
    except ConfigError as exc:
        raise InitError(f"the new topics file would be invalid: {exc}") from None
    text = render(data, answers)
    try:
        current = output.read_text(encoding="utf-8") if output.exists() else None
    except (OSError, UnicodeDecodeError):
        current = ""
    state = "new" if current is None else "unchanged" if current == text else "overwrite"
    configured = os.environ.get("MYBUDDY_CONFIG", "").strip()
    elsewhere = None
    if configured and Path(configured).expanduser().absolute() != output:
        elsewhere = configured
    # Only add MYBUDDY_CONFIG when nothing sets it yet (never edit an existing setting).
    env_update = not configured and output != DEFAULT_CONFIG_PATH.absolute()
    return Plan(
        output,
        text,
        config.topics,
        config.extra_allowed_hosts,
        answers,
        state,
        env_file,
        env_update,
        elsewhere,
    )


def needed_env(plan: Plan) -> list[str]:
    names = ["GROQ_API_KEY"] if any(t.ai for t in plan.enabled) else []
    return names + list(CHANNEL_ENV[plan.answers.delivery])


def print_plan(plan: Plan) -> None:
    print()
    print("MyBuddy setup plan")
    print("  Timezone: Asia/Kolkata (IST), fixed")
    print(f"  Delivery: {plan.answers.delivery}")
    print(f"  Topics enabled ({len(plan.enabled)} of {len(plan.topics)}):")
    for t in plan.enabled:
        names = [str(spec.get("name")) for _, spec in topic_source_specs([t])]
        hosts = sorted(source_hosts([t]))
        print(f"    {t.emoji} {t.id:<18} {t.title:<34} {schedule_label(t.schedule)}")
        print(f"       sources: {', '.join(names) or '(built from other reports)'}")
        print(f"       hosts:   {', '.join(hosts) or '(none)'}")
    off = [t.id for t in plan.topics if not t.enabled]
    if off:
        print(f"  Turned off (kept in the file as enabled: false): {', '.join(off)}")
    service = []
    if any(t.ai for t in plan.enabled):
        service.append(url_host(GROQ_URL) or "api.groq.com")
    if plan.answers.delivery == "telegram":
        service.append(url_host(TELEGRAM_API) or "api.telegram.org")
    if plan.answers.delivery == "discord":
        service.append("your Discord webhook's host")
    all_hosts = sorted(source_hosts(plan.enabled) | set(plan.extra_hosts))
    print(f"  Hosts the app may contact: {', '.join(all_hosts + service)}")
    print("  Files:")
    if plan.file_state == "unchanged":
        print(f"    keep    {plan.output} (already up to date)")
    elif plan.file_state == "new":
        print(f"    write   {plan.output} (mode 600, folder 700)")
    else:
        print(f"    OVERWRITE {plan.output} (mode 600)")
        print(f"            old version kept in {plan.output.parent / BACKUP_DIR}/ (mode 600)")
        print("            a running scheduler reloads it within a minute")
    if plan.env_update:
        print(f"    update  {plan.env_file.expanduser()}: add MYBUDDY_CONFIG={plan.output}")
        print("            (only that line is added; the file stays mode 600)")
    if plan.env_points_elsewhere:
        print(
            f"  Note: MYBUDDY_CONFIG is {plan.env_points_elsewhere}, so the app will not read "
            "this file (the env file is left alone)."
        )
    print("  Secrets and ids still needed (names only; never type them here):")
    names = needed_env(plan)
    for name in names:
        print(f"    {name:<20} {'set' if os.environ.get(name, '').strip() else 'missing'}")
    if not names:
        print("    none (the outbox channel writes reports to files)")
    print("  Set secrets from your laptop with: MYBUDDY_HOST=<vm-ip> bash deploy/set-secrets.sh")
    if plan.answers.delivery == "telegram":
        print("  Then message your bot and run: mybuddy telegram-setup --save")
    print("  Finally check everything with: mybuddy doctor")
    print()


def apply_plan(plan: Plan, settings: Settings) -> None:
    if plan.file_state != "unchanged":
        folder = plan.output.parent
        make_private_dirs(folder)
        if plan.file_state == "overwrite":
            backups = ensure_private_dir(folder / BACKUP_DIR)
            stamp = datetime.now(IST).strftime("%Y%m%d-%H%M%S")
            backup = backups / f"{plan.output.name}.{stamp}"
            n = 1
            while backup.exists():
                n += 1
                backup = backups / f"{plan.output.name}.{stamp}-{n}"
            write_private(backup, plan.output.read_text(encoding="utf-8"))
            print(f"kept the old version as {backup}")
        write_private(plan.output, plan.text)
        load_config(plan.output)  # the file on disk must load exactly as validated
        print(f"wrote {plan.output}")
        if plan.file_state == "overwrite" and settings.data_dir.is_dir():
            write_private(settings.data_dir / RELOAD_MARKER, "init\n")  # the scheduler reloads
    if plan.env_update:
        path = plan.env_file.expanduser()
        lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
        lines = [ln for ln in lines if not _CONFIG_LINE.match(ln)]
        lines.append(f"MYBUDDY_CONFIG={plan.output}")
        write_private(path, "\n".join(lines) + "\n")
        print(f"set MYBUDDY_CONFIG in {path}")


# ---------------------------------------------------------------------- command


def cmd_init(
    env_file: Path,
    settings: Settings,
    *,
    base_path: Path | None = None,
    output: Path | None = None,
    answers_file: Path | None = None,
    assume_yes: bool = False,
    force: bool = False,
) -> int:
    output = (output or default_output()).expanduser().absolute()
    if assume_yes and answers_file is None:
        _err("--yes needs --answers FILE (the interactive setup always asks)")
        return 2
    # Start from the current file so its sources, limits and bot changes are kept.
    base_path = base_path or (output if output.exists() else DEFAULT_CONFIG_PATH)
    try:
        base_config = load_config(base_path)
        base = yaml.safe_load(base_path.expanduser().read_text(encoding="utf-8"))
    except (ConfigError, OSError, yaml.YAMLError) as exc:
        _err(f"base topics file: {exc}")
        return 2
    try:
        if answers_file is not None:
            answers = load_answers(answers_file, base_config.topics)
        else:
            answers = ask_answers(base_config.topics)
        plan = make_plan(base, answers, output, env_file)
    except InitError as exc:
        _err(f"init: {exc}; nothing was written")
        return 2
    except Abort:
        _err("init: cancelled; nothing was written")
        return 1
    print_plan(plan)
    if plan.nothing_to_do:
        print("Already set up; nothing to write.")
        return 0
    if assume_yes:
        if plan.file_state == "overwrite" and not force:
            _err(f"init: {output} exists and differs; add --force to overwrite it")
            return 1
    else:
        try:
            reply = input('Type "yes" to write these files (anything else cancels): ')
        except EOFError:
            reply = ""
        if reply != "yes":
            print("Cancelled; nothing was written.")
            return 1
    try:
        apply_plan(plan, settings)
    except (OSError, UnicodeDecodeError, ConfigError) as exc:
        _err(f"init: could not write: {exc}")
        return 1
    print("Done. Next: set the secrets listed above, then run: mybuddy doctor")
    return 0
