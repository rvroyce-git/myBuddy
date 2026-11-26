"""mybuddy doctor: check everything at once and print one PASS/WARN/FAIL line per check.

Exit code 1 when any check FAILs (WARN and SKIP never fail). Secret values are never read
into the output: environment variables are reported by name only, the Telegram token only
travels inside the getMe request and the Groq key only in its Authorization header.
`mybuddy check` stays as the older, more verbose subset; staleness, failed sources and the
access mode reuse the health check (reports.py) and `check`'s logic rather than copies.
"""

from __future__ import annotations

import json
import os
import shutil
import sqlite3
import stat
import subprocess
import tempfile
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from mybuddy.allowlist import build_allowed_hosts
from mybuddy.cli.check import _disallowed_urls, access_lines, groq_check, telegram_check
from mybuddy.config import DEFAULT_CONFIG_PATH, ConfigError, Settings, Topic, load_config
from mybuddy.configedit import RELOAD_MARKER
from mybuddy.models import Clock
from mybuddy.reports import OK_STATUSES, collect_health, stale_after
from mybuddy.schedule import IST
from mybuddy.store import Store

STALE_AFTER = timedelta(hours=26)  # reports.json older than this: the site is stale
DISK_WARN_BYTES = 1024**3
DISK_FAIL_BYTES = 200 * 1024**2
MEM_WARN_KB = 150 * 1024
MEM_FAIL_KB = 64 * 1024
# (unit, required): the publish path unit is only enabled once the deploy key exists.
SYSTEMD_UNITS: tuple[tuple[str, bool], ...] = (
    ("mybuddy.service", True),
    ("mybuddy-bot.service", True),
    ("mybuddy-publish.path", False),
)

# Swappable for tests (no real system calls in the test suite).
MEMINFO = Path("/proc/meminfo")
disk_usage: Callable[[Path], Any] = shutil.disk_usage
which: Callable[[str], str | None] = shutil.which


def systemctl_state(unit: str) -> str:
    """`systemctl is-active UNIT` output ("active", "inactive", "failed", ...)."""
    try:
        proc = subprocess.run(
            ["systemctl", "is-active", unit],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return f"error ({type(exc).__name__})"
    return (proc.stdout or proc.stderr).strip()[:40] or "unknown"


@dataclass(frozen=True)
class Result:
    level: str  # PASS | WARN | FAIL | SKIP
    name: str
    detail: str


class Doctor:
    def __init__(self, config_path: Path, settings: Settings, clock: Clock, env_file: Path) -> None:
        self.config_path = config_path
        self.settings = settings
        self.clock = clock
        self.env_file = env_file
        self.results: list[Result] = []
        self.topics: list[Topic] = []
        self.extra_hosts: frozenset[str] = frozenset()

    def add(self, level: str, name: str, detail: str) -> None:
        self.results.append(Result(level, name, detail))

    # ------------------------------------------------------------------ checks
    def check_config(self) -> None:
        """The config the services use (MYBUDDY_CONFIG: the live, bot-editable copy)."""
        try:
            config = load_config(self.config_path)
        except ConfigError as exc:
            self.add("FAIL", "config", str(exc))
            return
        self.topics, self.extra_hosts = config.topics, config.extra_allowed_hosts
        on = [t.id for t in self.topics if t.enabled]
        off = len(self.topics) - len(on)
        extra = f"; {off} turned off" if off else ""
        self.add("PASS", "config", f"{self.config_path}: {len(on)} topics on{extra}")
        path = self.config_path.expanduser()
        if path.absolute() == DEFAULT_CONFIG_PATH.absolute():
            self.add(
                "WARN",
                "live config",
                "using the repo's config/topics.yaml; set MYBUDDY_CONFIG to the live copy "
                "(deploy.sh seeds ~/.config/mybuddy/config/topics.yaml) for /set changes",
            )
            return
        mode = _mode(path)
        if mode is not None and mode & 0o077:
            self.add("WARN", "live config", f"{path} is mode {mode:o}; run: chmod 600 {path}")
        elif (self.settings.data_dir / RELOAD_MARKER).exists():
            self.add("WARN", "live config", "a reload is pending (is the scheduler running?)")
        else:
            self.add("PASS", "live config", f"{path} is mode {mode:o}" if mode else str(path))

    def check_env_vars(self) -> None:
        s = self.settings
        if s.fake:
            self.add("WARN", "env", "MYBUDDY_FAKE is on: AI and delivery are simulated")
        names = ["GROQ_API_KEY", "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID", "DISCORD_WEBHOOK_URL"]
        present = [n for n in names if os.environ.get(n, "").strip()]
        missing = [n for n in names if n not in present]
        detail = f"set: {', '.join(present) or 'none'}; not set: {', '.join(missing) or 'none'}"
        problems: list[str] = []
        if any(t.ai for t in self.topics) and not (s.groq_api_key or s.gemini_api_key):
            problems.append("no AI key, reports use the plain fallback")
        telegram = bool(s.telegram_bot_token and s.telegram_chat_id)
        if not telegram and not s.discord_webhook_url:
            problems.append("no Telegram or Discord, reports go to the outbox folder")
        if s.telegram_bot_token and not s.telegram_chat_id:
            problems.append("TELEGRAM_CHAT_ID missing: run 'mybuddy telegram-setup --save'")
        if problems:
            self.add("WARN", "env vars", f"{detail} ({'; '.join(problems)})")
        else:
            self.add("PASS", "env vars", detail)

    def check_access(self) -> None:
        """Private by default: who may subscribe and chat (counts only, never chat ids)."""
        lines = access_lines(self.settings)
        warnings = [text for kind, text in lines if kind == "warning"]
        info = "; ".join(text for kind, text in lines if kind == "info")
        self.add("WARN" if warnings else "PASS", "access", "; ".join([info, *warnings]))

    def check_permissions(self) -> None:
        env = self.env_file.expanduser()
        mode = _mode(env)
        if mode is None:
            self.add("WARN", "env file", f"{env} not found (settings come from the environment)")
        elif mode & 0o077:
            self.add("FAIL", "env file", f"{env} is mode {mode:o}; run: chmod 600 {env}")
        else:
            self.add("PASS", "env file", f"{env} is mode {mode:o}")
        data = self.settings.data_dir
        mode = _mode(data)
        if mode is None:
            self.add("WARN", "data dir", f"{data} does not exist yet (created on the first run)")
        elif mode & 0o077:
            self.add("FAIL", "data dir", f"{data} is mode {mode:o}; run: chmod 700 {data}")
        else:
            self.add("PASS", "data dir", f"{data} is mode {mode:o}")

    def check_writable(self) -> None:
        data = self.settings.data_dir
        if not data.is_dir():
            self.add("SKIP", "data writable", f"{data} does not exist yet")
            return
        try:
            with tempfile.TemporaryFile(dir=data):
                pass
        except OSError as exc:
            self.add("FAIL", "data writable", f"{data}: {exc.strerror or exc}")
            return
        self.add("PASS", "data writable", str(data))

    def check_database(self) -> None:
        path = self.settings.data_dir / "mybuddy.db"
        if not path.is_file():
            self.add("WARN", "database", f"{path} not found (nothing has run yet)")
            self.add("SKIP", "last runs", "no database")
            return
        try:  # read-only first: a damaged file is reported, never "repaired"
            db = sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True, timeout=5)
            try:
                rows = db.execute("PRAGMA integrity_check").fetchall()
            finally:
                db.close()
        except sqlite3.Error as exc:
            self.add("FAIL", "database", f"{path}: {exc}")
            return
        result = ", ".join(str(r[0]) for r in rows[:3])
        if result != "ok":
            self.add("FAIL", "database", f"{path}: integrity check: {result}")
            return
        self.add("PASS", "database", f"{path}: integrity ok")
        self.check_runs()

    def check_runs(self) -> None:
        """Last successful run per topic and failed sources, from the health check's run log."""
        now = self.clock.now()
        try:
            with Store(self.settings.data_dir) as store:
                health = collect_health(self.topics, store, self.settings, now)
                own = [t for t in self.topics if t.kind == "health"]
                own_last = {t.id: store.last_run(t.id, OK_STATUSES) for t in own}
        except (sqlite3.Error, OSError) as exc:
            self.add("FAIL", "last runs", f"cannot read the run log: {exc}")
            return
        rows = [(row.topic, row.last_ok, row.stale) for row in health.topics]
        for t in own:  # the health report itself (collect_health leaves it out)
            last = own_last[t.id]
            rows.append((t, last, last is not None and now - last > stale_after(t.schedule)))
        for topic, last, stale in rows:
            name = f"last run {topic.id}"
            if not topic.enabled:
                self.add("SKIP", name, "turned off (enabled: false)")
            elif last is None:
                self.add("WARN", name, "never ran")
            elif stale:
                limit = stale_after(topic.schedule)
                self.add("FAIL", name, f"{_age(now - last)} ago (stale after {_age(limit)})")
            else:
                self.add("PASS", name, f"{_age(now - last)} ago")
        if health.failures:
            parts = [
                f"{topic}/{source} x{n}" for (topic, source), (n, _) in health.failures.items()
            ]
            self.add("WARN", "sources", f"failed in the last 24 h: {', '.join(parts)}")
        else:
            self.add("PASS", "sources", "no failed sources in the last 24 h")

    def check_publish(self) -> None:
        path = self.settings.site_data
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            self.add("WARN", "publish", f"{path} not found (written after the first report)")
            return
        except (OSError, UnicodeDecodeError, ValueError) as exc:
            self.add("FAIL", "publish", f"{path}: unreadable: {type(exc).__name__}")
            return
        when = _parse_time(data.get("generated_at") if isinstance(data, dict) else None)
        if when is None:
            self.add("FAIL", "publish", f"{path}: no valid generated_at")
            return
        age = self.clock.now() - when
        if age > STALE_AFTER:
            self.add("WARN", "publish", f"reports.json is {_age(age)} old")
        else:
            self.add("PASS", "publish", f"reports.json is {_age(age)} old")

    def check_hosts(self) -> None:
        allowed = build_allowed_hosts(self.topics, self.settings, self.extra_hosts)
        bad = _disallowed_urls(self.topics, self.settings, allowed)
        for problem in bad:
            self.add("FAIL", "host allowlist", problem)
        if not bad:
            self.add("PASS", "host allowlist", f"{len(allowed)} hosts cover every config URL")

    def check_groq(self) -> None:
        if not self.settings.groq_api_key or self.settings.fake:
            self.add("SKIP", "groq", "GROQ_API_KEY not set" if not self.settings.fake else "fake")
            return
        allowed = build_allowed_hosts(self.topics, self.settings, self.extra_hosts)
        ok, problem = groq_check(self.settings, allowed)
        if problem:
            self.add("FAIL", "groq", problem)
        else:
            self.add("PASS", "groq", f"{ok}; {self.settings.groq_model} offered")

    def check_telegram(self) -> None:
        if not self.settings.telegram_bot_token or self.settings.fake:
            reason = "fake" if self.settings.fake else "TELEGRAM_BOT_TOKEN not set"
            self.add("SKIP", "telegram", reason)
            return
        ok, warning, problem = telegram_check(self.settings)
        if problem:
            self.add("FAIL", "telegram", problem)
        elif warning:
            self.add("WARN", "telegram", f"{ok}; {warning}")
        else:
            self.add("PASS", "telegram", ok or "reachable")

    def check_disk(self) -> None:
        target = self.settings.data_dir
        while not target.exists() and target != target.parent:
            target = target.parent
        try:
            free = int(disk_usage(target).free)
        except OSError as exc:
            self.add("WARN", "disk", f"cannot measure free space: {exc.strerror or exc}")
            return
        detail = f"{free / 1024**3:.1f} GB free at {target}"
        if free < DISK_FAIL_BYTES:
            self.add("FAIL", "disk", detail)
        elif free < DISK_WARN_BYTES:
            self.add("WARN", "disk", detail)
        else:
            self.add("PASS", "disk", detail)

    def check_memory(self) -> None:
        try:
            text = MEMINFO.read_text(encoding="ascii")
        except (OSError, UnicodeDecodeError):
            self.add("SKIP", "memory", f"{MEMINFO} not available (not Linux)")
            return
        info = {}
        for line in text.splitlines():
            key, _, rest = line.partition(":")
            parts = rest.split()
            if parts and parts[0].isdigit():
                info[key.strip()] = int(parts[0])
        avail = info.get("MemAvailable")
        if avail is None:
            self.add("SKIP", "memory", "MemAvailable not reported")
            return
        total = info.get("MemTotal", 0)
        detail = f"{avail // 1024} MB available of {total // 1024} MB"
        if avail < MEM_FAIL_KB:
            self.add("FAIL", "memory", detail)
        elif avail < MEM_WARN_KB:
            self.add("WARN", "memory", detail)
        else:
            self.add("PASS", "memory", detail)

    def check_systemd(self) -> None:
        if not which("systemctl"):
            self.add("SKIP", "systemd", "systemctl not found (not a systemd host)")
            return
        for unit, required in SYSTEMD_UNITS:
            state = systemctl_state(unit)
            if state == "active":
                self.add("PASS", f"unit {unit}", state)
            else:
                self.add("FAIL" if required else "WARN", f"unit {unit}", state)

    # ------------------------------------------------------------------ run
    def run(self) -> list[Result]:
        self.check_config()
        self.check_env_vars()
        self.check_access()
        self.check_permissions()
        self.check_writable()
        self.check_database()
        self.check_publish()
        self.check_hosts()
        self.check_groq()
        self.check_telegram()
        self.check_disk()
        self.check_memory()
        self.check_systemd()
        return self.results


def _parse_time(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        when = datetime.fromisoformat(value)
    except ValueError:
        return None
    return when if when.tzinfo else when.replace(tzinfo=IST)


def _mode(path: Path) -> int | None:
    try:
        return stat.S_IMODE(path.stat().st_mode)
    except OSError:
        return None


def _age(delta: timedelta) -> str:
    hours = delta.total_seconds() / 3600
    if hours < 1:
        return f"{max(0, int(delta.total_seconds() // 60))} min"
    if hours < 48:
        return f"{hours:.1f} h"
    return f"{hours / 24:.1f} days"


def print_results(results: Sequence[Result]) -> None:
    width = max((len(r.name) for r in results), default=0)
    for r in results:
        print(f"{r.level:<4}  {r.name:<{width}}  {r.detail}")
    counts = {level: sum(r.level == level for r in results) for level in ("PASS", "WARN", "FAIL")}
    skipped = sum(r.level == "SKIP" for r in results)
    print(
        f"doctor: {counts['PASS']} passed, {counts['WARN']} warnings, {counts['FAIL']} failed"
        + (f", {skipped} skipped" if skipped else "")
    )


def cmd_doctor(config_path: Path, settings: Settings, clock: Clock, env_file: Path) -> int:
    results = Doctor(config_path, settings, clock, env_file).run()
    print_results(results)
    return 1 if any(r.level == "FAIL" for r in results) else 0
