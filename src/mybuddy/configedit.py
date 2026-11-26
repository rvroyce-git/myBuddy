"""Owner-only config changes from the Telegram bot, applied only after explicit approval.

No AI is involved: requests use a small fixed grammar, parsed here.

  /topic on|off <id>               /time <id> HH:MM
  /source add <topic> <https-url>  /source remove <topic> <source name>
  /limit chat-daily|chat-rate|chat-tokens <n>
  /set <any of the above>          /undo (restore the newest backup)

Each request gets a preview (a YAML diff of the config file, or the limit change), the result
of validating it with the real config loader, and a change id. Nothing is written until the
owner sends `/approve <id>` within APPROVAL_SECONDS; `/reject <id>` cancels it. Applying a
config change keeps a timestamped backup (0600), replaces the file atomically (0600) and
writes a reload marker the scheduler picks up within a minute. New source URLs must be
https; their host is added to `extra_allowed_hosts`. Nothing is fetched while previewing.
Secrets are never readable or settable here: limits are the only settings, stored as
bounded overrides in the database.
"""

from __future__ import annotations

import contextlib
import difflib
import hashlib
import os
import re
import secrets
import sys
import tempfile
import urllib.parse
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, TextIO

import yaml

from mybuddy.config import (
    MAX_CHAT_DAILY_TOKEN_BUDGET,
    Config,
    ConfigError,
    Topic,
    validate_config_text,
)
from mybuddy.export import schedule_label
from mybuddy.http import host_allowlist, url_host
from mybuddy.models import Clock
from mybuddy.store import Store, ensure_private_dir

APPROVAL_SECONDS = 600
RELOAD_MARKER = "reload-config"
BACKUP_DIR = "backups"
BACKUPS_KEPT = 20
DIFF_MAX_CHARS = 2500
URL_MAX_CHARS = 300
# /limit name -> (override key, minimum, maximum). Groq's free tier is ~200K tokens/day for
# everything, so chat can never take more than MAX_CHAT_DAILY_TOKEN_BUDGET (80K).
LIMITS: dict[str, tuple[str, int, int]] = {
    "chat-daily": ("chat_daily_cap", 0, 1000),
    "chat-rate": ("chat_rate_per_min", 0, 60),
    "chat-tokens": ("chat_daily_token_budget", 0, MAX_CHAT_DAILY_TOKEN_BUDGET),
}
_ID_RE = re.compile(r"[a-z][a-z0-9_]{0,39}")
_TIME_RE = re.compile(r"([01][0-9]|2[0-3]):[0-5][0-9]")
_CHANGE_ID_RE = re.compile(r"[0-9a-f]{6}")
_NAME_RE = re.compile(r"[^\x00-\x1f\x7f]{1,60}")

USAGE = (
    "Config commands (owner only; each needs /approve):\n"
    "/topic on|off <id>\n"
    "/time <id> HH:MM\n"
    "/source add <topic> <https feed url>\n"
    "/source remove <topic> <source name>\n"
    "/limit chat-daily|chat-rate|chat-tokens <n>\n"
    "/undo – restore the newest backup\n"
    "/config – current settings · /changes – recent changes"
)


class ChangeError(Exception):
    """A request that cannot become a change (bad grammar or invalid result)."""


@dataclass
class Pending:
    change_id: str
    summary: str
    created: datetime
    base_hash: str = ""
    new_text: str | None = None  # config changes
    limit: tuple[str, int] | None = None  # (override key, value)
    extra: dict[str, Any] = field(default_factory=dict)


def _hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _dump(data: Any) -> str:
    return yaml.safe_dump(data, sort_keys=False, allow_unicode=True, width=100)


def write_private(path: Path, text: str) -> None:
    """Atomically replace `path` with a 0600 file."""
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise


def check_feed_url(url: str) -> str:
    """https only, a plain host name (no IP, port or credentials), no spaces. Returns host."""
    if len(url) > URL_MAX_CHARS or not url.startswith("https://") or re.search(r"\s", url):
        raise ChangeError("the feed URL must start with https:// (no spaces, max 300 chars)")
    try:
        parts = urllib.parse.urlsplit(url)
        port = parts.port
    except ValueError:
        raise ChangeError("that feed URL cannot be parsed") from None
    host = url_host(url)
    if not host or port is not None or parts.username or parts.password:
        raise ChangeError("the feed URL needs a plain host name (no port or login)")
    try:
        host_allowlist([host])
    except ValueError:
        raise ChangeError("the feed URL must use a host name, not an IP address") from None
    return host


class ConfigEditor:
    def __init__(
        self,
        config_path: Path,
        store: Store,
        clock: Clock,
        *,
        marker_path: Path,
        validate: Callable[[str], Config] | None = None,
        log: TextIO | None = None,
    ) -> None:
        self.path = config_path.expanduser()
        self.store = store
        self.clock = clock
        self.marker_path = marker_path
        self.validate = validate or (lambda text: validate_config_text(text, origin=self.path.name))
        self.log = log
        self.pending: dict[str, Pending] = {}

    def _log(self, msg: str) -> None:
        print(f"[mybuddy-bot] {msg}", file=self.log or sys.stderr, flush=True)

    # ------------------------------------------------------------------ reading
    def _read(self) -> tuple[str, dict[str, Any]]:
        text = self.path.read_text(encoding="utf-8")
        data = yaml.safe_load(text)
        if not isinstance(data, dict) or not isinstance(data.get("topics"), list):
            raise ChangeError("the config file has no topics list")
        return text, data

    @staticmethod
    def _topic(data: dict[str, Any], topic_id: str) -> dict[str, Any]:
        for entry in data["topics"]:
            if isinstance(entry, dict) and entry.get("id") == topic_id:
                return entry
        raise ChangeError(f"no topic with id {topic_id!r}")

    def show(self, extra_lines: Sequence[str] = ()) -> str:
        """Safe summary of the current config (no secrets, no chat ids)."""
        try:
            text, _ = self._read()
            topics: list[Topic] = self.validate(text).topics
        except (OSError, ChangeError, ConfigError) as exc:
            return f"Cannot read the config: {str(exc)[:200]}"
        lines = [f"Config: {self.path.name}"]
        for t in topics:
            n = len(t.sources) or sum(len(e.sources) for e in (t.rotation or {}).values())
            state = "on" if t.enabled else "OFF"
            lines.append(f"• {t.id}: {state}, {schedule_label(t.schedule)}, {n} source(s)")
        lines += list(extra_lines)
        return "\n".join(lines)

    # ------------------------------------------------------------------ requests
    def request(self, command: str, words: Sequence[str]) -> str:
        """Parse one request and reply with its preview (or why it can't be done)."""
        self._expire()
        try:
            if command == "set":
                if not words or words[0].lower() == "help":
                    return USAGE
                command, words = words[0].lstrip("/").lower(), list(words[1:])
            if command == "limit":
                return self._preview_limit(words)
            if command == "undo":
                return self._preview_config(*self._undo())
            builders = {"topic": self._topic_onoff, "time": self._time, "source": self._source}
            if command not in builders:
                raise ChangeError("unknown change")
            text, data = self._read()
            summary = builders[command](data, list(words))
            return self._preview_config(text, _dump(data), summary)
        except ChangeError as exc:
            return f"Not changed: {exc}.\n\n{USAGE}"
        except OSError as exc:
            return f"Not changed: cannot read the config ({type(exc).__name__})."

    def _topic_onoff(self, data: dict[str, Any], words: list[str]) -> str:
        if len(words) != 2 or words[0] not in ("on", "off") or not _ID_RE.fullmatch(words[1]):
            raise ChangeError("use /topic on <id> or /topic off <id>")
        topic = self._topic(data, words[1])
        if words[0] == "off":
            topic["enabled"] = False
        else:
            topic.pop("enabled", None)
        return f"topic {words[0]} {words[1]}"

    def _time(self, data: dict[str, Any], words: list[str]) -> str:
        if len(words) != 2 or not _ID_RE.fullmatch(words[0]) or not _TIME_RE.fullmatch(words[1]):
            raise ChangeError("use /time <id> HH:MM (24-hour IST)")
        topic = self._topic(data, words[0])
        schedule = topic.get("schedule")
        if not isinstance(schedule, dict) or "every_minutes" in schedule:
            raise ChangeError(f"{words[0]} runs every few minutes; /time only fits daily topics")
        schedule["time"] = words[1]
        return f"time {words[0]} {words[1]}"

    def _source(self, data: dict[str, Any], words: list[str]) -> str:
        if len(words) < 3 or words[0] not in ("add", "remove") or not _ID_RE.fullmatch(words[1]):
            raise ChangeError("use /source add <topic> <url> or /source remove <topic> <name>")
        topic = self._topic(data, words[1])
        sources = topic.get("sources")
        if not isinstance(sources, list):
            raise ChangeError(f"{words[1]} has no plain source list (it uses a rotation)")
        names = [s.get("name") for s in sources if isinstance(s, dict)]
        if words[0] == "remove":
            name = " ".join(words[2:])
            if name not in names:
                raise ChangeError(f"{words[1]} has no source named {name[:60]!r}")
            topic["sources"] = [
                s for s in sources if not (isinstance(s, dict) and s.get("name") == name)
            ]
            return f"source remove {words[1]} {name[:60]}"
        if len(words) != 3:
            raise ChangeError("use /source add <topic> <https feed url>")
        url = words[2]
        host = check_feed_url(url)
        if any(isinstance(s, dict) and s.get("url") == url for s in sources):
            raise ChangeError("that feed is already a source of this topic")
        name, n = host, 2
        while name in names:
            name, n = f"{host} {n}", n + 1
        sources.append({"type": "rss", "name": name, "url": url})
        hosts = data.get("extra_allowed_hosts") or []
        if not isinstance(hosts, list):
            raise ChangeError("extra_allowed_hosts is not a list")
        if host not in hosts:
            data["extra_allowed_hosts"] = [*hosts, host]
        return f"source add {words[1]} {host}"

    def _undo(self) -> tuple[str, str, str]:
        backups = self.backups()
        if not backups:
            raise ChangeError("there is no backup to restore")
        newest = backups[-1]
        text = self.path.read_text(encoding="utf-8")
        return text, newest.read_text(encoding="utf-8"), f"undo: restore {newest.name}"

    def _preview_config(self, old_text: str, new_text: str, summary: str) -> str:
        try:
            config = self.validate(new_text)
        except ConfigError as exc:
            self._log(f"config change rejected by validation: {summary.split(' ')[0]}")
            raise ChangeError(f"the result would be invalid: {str(exc)[:300]}") from None
        try:
            old_norm = _dump(yaml.safe_load(old_text))
        except yaml.YAMLError:
            old_norm = old_text
        new_norm = _dump(yaml.safe_load(new_text))
        diff = "".join(
            difflib.unified_diff(
                old_norm.splitlines(keepends=True),
                new_norm.splitlines(keepends=True),
                self.path.name,
                f"{self.path.name} (proposed)",
                n=1,
            )
        )
        if not diff:
            raise ChangeError("that is already the current setting")
        if len(diff) > DIFF_MAX_CHARS:
            diff = diff[:DIFF_MAX_CHARS] + "\n… (diff shortened)"
        pending = self._add(summary, base_hash=_hash(old_text), new_text=new_text)
        return (
            f"Change {pending.change_id}: {summary}\n\n{diff.rstrip()}\n\n"
            f"Validation: ok ({len(config.topics)} topics). Nothing was fetched.\n"
            f"Send /approve {pending.change_id} within {APPROVAL_SECONDS // 60} minutes to apply, "
            f"or /reject {pending.change_id}."
        )

    def _preview_limit(self, words: Sequence[str]) -> str:
        if len(words) != 2 or words[0] not in LIMITS or not re.fullmatch(r"[0-9]{1,7}", words[1]):
            raise ChangeError("use /limit chat-daily|chat-rate|chat-tokens <whole number>")
        key, low, high = LIMITS[words[0]]
        value = int(words[1])
        if not low <= value <= high:
            raise ChangeError(f"{words[0]} must be between {low} and {high}")
        current = self.store.limit_overrides().get(key)
        summary = f"limit {words[0]} {value}"
        pending = self._add(summary, limit=(key, value))
        was = "the setting's value" if current is None else str(current)
        return (
            f"Change {pending.change_id}: {summary}\n\n"
            f"{key.upper()}: {was} -> {value} (stored as an override in the database; "
            "the env file is not touched).\n"
            f"Send /approve {pending.change_id} within {APPROVAL_SECONDS // 60} minutes to apply, "
            f"or /reject {pending.change_id}."
        )

    def _add(self, summary: str, **kw: Any) -> Pending:
        change_id = secrets.token_hex(3)
        while change_id in self.pending:
            change_id = secrets.token_hex(3)
        pending = Pending(change_id, summary, self.clock.now(), **kw)
        self.pending[change_id] = pending
        self.store.log_config_change(change_id, summary, "proposed", pending.created)
        self._log(f"config change {change_id} proposed: {summary.split(' ')[0]}")
        return pending

    def _expire(self) -> None:
        now = self.clock.now()
        for cid, p in list(self.pending.items()):
            if (now - p.created).total_seconds() > APPROVAL_SECONDS:
                del self.pending[cid]
                self.store.log_config_change(cid, p.summary, "expired", now)

    # ------------------------------------------------------------------ decisions
    def approve(self, change_id: str) -> str:
        self._expire()
        change_id = change_id.strip().lower()
        pending = self.pending.pop(change_id, None) if _CHANGE_ID_RE.fullmatch(change_id) else None
        if pending is None:
            return "No such pending change (it may have expired). Send the request again."
        now = self.clock.now()
        try:
            if pending.limit is not None:
                self.store.set_limit_override(*pending.limit)
            else:
                self._apply(pending)
        except ChangeError as exc:
            self.store.log_config_change(change_id, pending.summary, "failed", now)
            return f"Not applied: {exc}."
        except OSError as exc:
            self.store.log_config_change(change_id, pending.summary, "failed", now)
            self._log(f"config change {change_id} failed: {type(exc).__name__}")
            return f"Not applied: cannot write the config ({type(exc).__name__})."
        self.store.log_config_change(change_id, pending.summary, "applied", now)
        self._log(f"config change {change_id} applied: {pending.summary.split(' ')[0]}")
        if pending.limit is not None:
            return f"Applied {change_id}: {pending.summary}."
        return (
            f"Applied {change_id}: {pending.summary}. The scheduler reloads the config within a "
            "minute. /undo restores the previous version."
        )

    def _apply(self, pending: Pending) -> None:
        current = self.path.read_text(encoding="utf-8")
        if _hash(current) != pending.base_hash:
            raise ChangeError("the config changed since the preview; send the request again")
        assert pending.new_text is not None
        self.validate(pending.new_text)  # still valid (prompt files etc. may have changed)
        backup_dir = ensure_private_dir(self.path.parent / BACKUP_DIR)
        stamp = self.clock.now().strftime("%Y%m%d-%H%M%S")
        backup = backup_dir / f"{self.path.name}.{stamp}"
        n = 1
        while backup.exists():
            n += 1
            backup = backup_dir / f"{self.path.name}.{stamp}-{n}"
        write_private(backup, current)
        write_private(self.path, pending.new_text)
        write_private(self.marker_path, f"{pending.change_id}\n")
        for old in self.backups()[:-BACKUPS_KEPT]:
            with contextlib.suppress(OSError):
                old.unlink()

    def reject(self, change_id: str) -> str:
        self._expire()
        change_id = change_id.strip().lower()
        pending = self.pending.pop(change_id, None) if _CHANGE_ID_RE.fullmatch(change_id) else None
        if pending is None:
            return "No such pending change."
        self.store.log_config_change(change_id, pending.summary, "rejected", self.clock.now())
        self._log(f"config change {change_id} rejected")
        return f"Rejected {change_id}. Nothing changed."

    def changes(self) -> str:
        self._expire()
        rows = self.store.config_changes(10)
        if not rows:
            return "No config changes yet."
        lines = ["Recent config changes (newest first, UTC):"]
        lines += [
            f"• {at[:16].replace('T', ' ')} {cid} {status}: {s}" for cid, at, s, status in rows
        ]
        return "\n".join(lines)

    def backups(self) -> list[Path]:
        folder = self.path.parent / BACKUP_DIR
        if not folder.is_dir():
            return []
        return sorted(p for p in folder.glob(f"{self.path.name}.*") if p.is_file())


def make_reloader(
    config_path: Path,
    marker_path: Path,
    load: Callable[[Path], Config],
    on_reload: Callable[[Config], None],
    *,
    log: TextIO | None = None,
) -> Callable[[], list[Topic] | None]:
    """For the scheduler: when the marker exists, remove it, load the config and hand it on.

    An invalid config is logged and ignored (the running schedule stays)."""

    def reload() -> list[Topic] | None:
        if not marker_path.exists():
            return None
        with contextlib.suppress(FileNotFoundError):
            marker_path.unlink()
        try:
            config = load(config_path)
        except ConfigError as exc:
            print(f"[mybuddy] config reload skipped: {exc}", file=log or sys.stderr, flush=True)
            return None
        on_reload(config)
        return config.topics

    return reload
