"""SQLite state: seen URLs (dedupe), source snapshots, saved reports and Telegram subscribers."""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import sqlite3
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

SEEN_RETENTION_DAYS = 30
REPORTS_PER_TOPIC = 30
RUNS_RETENTION_DAYS = 14  # run log and source failures, for the health check
ERROR_TEXT_MAX = 200

_SCHEMA = """
CREATE TABLE IF NOT EXISTS seen (
    topic_id TEXT NOT NULL,
    url_hash TEXT NOT NULL,
    first_seen TEXT NOT NULL,
    PRIMARY KEY (topic_id, url_hash)
);
CREATE INDEX IF NOT EXISTS seen_first_seen ON seen(first_seen);
CREATE TABLE IF NOT EXISTS snapshots (
    key TEXT PRIMARY KEY,
    json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS reports (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    topic_id TEXT NOT NULL,
    created_at TEXT NOT NULL,
    json TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS reports_topic ON reports(topic_id, id);
-- Public Telegram subscribers: only the chat id and topic ids, nothing about the person.
CREATE TABLE IF NOT EXISTS subscribers (
    chat_id INTEGER PRIMARY KEY,
    topics TEXT NOT NULL DEFAULT '[]',
    active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
-- Run log for the health check: one row per real (non dry-run) topic run.
CREATE TABLE IF NOT EXISTS runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    topic_id TEXT NOT NULL,
    at TEXT NOT NULL,
    status TEXT NOT NULL,
    collected INTEGER NOT NULL DEFAULT 0,
    new INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS runs_topic ON runs(topic_id, at);
CREATE TABLE IF NOT EXISTS source_failures (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    topic_id TEXT NOT NULL,
    source TEXT NOT NULL,
    at TEXT NOT NULL,
    error TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS source_failures_at ON source_failures(at);
"""

BOT_STATE_KEY = "telegram:bot"


@dataclass(frozen=True)
class StoredReport:
    topic_id: str
    created_at: datetime  # timezone-aware
    data: dict[str, Any]


@dataclass(frozen=True)
class SourceFailure:
    topic_id: str
    source: str
    at: datetime
    error: str


@dataclass(frozen=True)
class Subscriber:
    chat_id: int
    topics: tuple[str, ...]
    active: bool


def _decode_topics(raw: str) -> tuple[str, ...]:
    try:
        data = json.loads(raw)
    except ValueError:
        return ()
    if not isinstance(data, list):
        return ()
    return tuple(t for t in data if isinstance(t, str))


def url_hash(url: str) -> str:
    """Stable hash of a URL, ignoring surrounding whitespace and a trailing slash."""
    norm = url.strip()
    if norm.endswith("/"):
        norm = norm[:-1]
    return hashlib.sha256(norm.encode("utf-8")).hexdigest()[:32]


def ensure_private_dir(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    with contextlib.suppress(OSError):
        os.chmod(path, 0o700)
    return path


def _utc_stamp(when: datetime) -> str:
    """Fixed-width UTC ISO string so text comparison in SQL orders correctly."""
    return when.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_stamp(value: object) -> datetime | None:
    """Parse a stored ISO timestamp (ours, or a report's created_at); aware UTC or None."""
    if not isinstance(value, str):
        return None
    text = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        when = datetime.fromisoformat(text)
    except ValueError:
        return None
    return when.replace(tzinfo=UTC) if when.tzinfo is None else when.astimezone(UTC)


class Store:
    def __init__(self, data_dir: Path) -> None:
        self.data_dir = ensure_private_dir(data_dir.expanduser())
        self.path = self.data_dir / "mybuddy.db"
        if not self.path.exists():
            fd = os.open(self.path, os.O_CREAT | os.O_WRONLY, 0o600)
            os.close(fd)
        old_umask = os.umask(0o077)
        try:
            self._db = sqlite3.connect(self.path, timeout=10)
            self._db.execute("PRAGMA journal_mode=WAL")
            self._db.execute("PRAGMA synchronous=NORMAL")
            self._db.executescript(_SCHEMA)
            self._db.commit()
        finally:
            os.umask(old_umask)
        for suffix in ("", "-wal", "-shm"):
            p = Path(str(self.path) + suffix)
            if p.exists():
                with contextlib.suppress(OSError):
                    os.chmod(p, 0o600)

    def close(self) -> None:
        self._db.close()

    def __enter__(self) -> Store:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # ---- seen
    def unseen(self, topic_id: str, urls: Iterable[str]) -> set[str]:
        """Return the subset of `urls` not yet seen for this topic."""
        out: set[str] = set()
        cur = self._db.cursor()
        for url in urls:
            row = cur.execute(
                "SELECT 1 FROM seen WHERE topic_id=? AND url_hash=?", (topic_id, url_hash(url))
            ).fetchone()
            if row is None:
                out.add(url)
        return out

    def is_seen(self, topic_id: str, url: str) -> bool:
        return not self.unseen(topic_id, [url])

    def mark_seen(self, topic_id: str, urls: Iterable[str], now: datetime) -> None:
        stamp = _utc_stamp(now)
        with self._db:
            self._db.executemany(
                "INSERT OR IGNORE INTO seen(topic_id, url_hash, first_seen) VALUES (?, ?, ?)",
                [(topic_id, url_hash(u), stamp) for u in urls],
            )

    def prune_seen(self, now: datetime, days: int = SEEN_RETENTION_DAYS) -> int:
        """Forget URLs first seen more than `days` ago. Returns rows removed."""
        cutoff = _utc_stamp(now - timedelta(days=days))
        with self._db:
            cur = self._db.execute("DELETE FROM seen WHERE first_seen < ?", (cutoff,))
        return cur.rowcount

    # ---- snapshots
    def load_snapshot(self, key: str) -> dict[str, Any]:
        row = self._db.execute("SELECT json FROM snapshots WHERE key=?", (key,)).fetchone()
        if row is None:
            return {}
        try:
            data = json.loads(row[0])
        except (ValueError, RecursionError):
            return {}
        return data if isinstance(data, dict) else {}

    def save_snapshot(self, key: str, data: dict[str, Any]) -> None:
        with self._db:
            self._db.execute(
                "INSERT INTO snapshots(key, json) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET json=excluded.json",
                (key, json.dumps(data, sort_keys=True)),
            )

    # ---- reports
    def save_report(self, topic_id: str, created_at: datetime, data: dict[str, Any]) -> None:
        with self._db:
            self._db.execute(
                "INSERT INTO reports(topic_id, created_at, json) VALUES (?, ?, ?)",
                (topic_id, created_at.isoformat(), json.dumps(data, ensure_ascii=False)),
            )
            self._db.execute(
                "DELETE FROM reports WHERE topic_id=? AND id NOT IN "
                "(SELECT id FROM reports WHERE topic_id=? ORDER BY id DESC LIMIT ?)",
                (topic_id, topic_id, REPORTS_PER_TOPIC),
            )

    def latest_report(self, topic_id: str) -> dict[str, Any] | None:
        row = self._db.execute(
            "SELECT json FROM reports WHERE topic_id=? ORDER BY id DESC LIMIT 1", (topic_id,)
        ).fetchone()
        if row is None:
            return None
        try:
            data = json.loads(row[0])
        except (ValueError, RecursionError):  # a damaged row must not break export
            return None
        return data if isinstance(data, dict) else None

    def reports_since(self, since: datetime) -> list[StoredReport]:
        """Saved reports created at or after `since`, oldest first; damaged rows skipped.
        The table is small (REPORTS_PER_TOPIC per topic), so filtering happens in Python."""
        rows = self._db.execute("SELECT topic_id, created_at, json FROM reports ORDER BY id")
        out: list[StoredReport] = []
        for topic_id, created, raw in rows.fetchall():
            when = _parse_stamp(created)
            if when is None or when < since:
                continue
            try:
                data = json.loads(raw)
            except (ValueError, RecursionError):
                continue
            if isinstance(data, dict):
                out.append(StoredReport(str(topic_id), when, data))
        return out

    def report_counts(self) -> dict[str, int]:
        rows = self._db.execute("SELECT topic_id, COUNT(*) FROM reports GROUP BY topic_id")
        return {str(t): int(n) for t, n in rows.fetchall()}

    def count_reports(self, topic_id: str) -> int:
        row = self._db.execute("SELECT COUNT(*) FROM reports WHERE topic_id=?", (topic_id,))
        return int(row.fetchone()[0])

    # ---- run log (health check)
    def record_run(
        self, topic_id: str, at: datetime, status: str, *, collected: int = 0, new: int = 0
    ) -> None:
        cutoff = _utc_stamp(at - timedelta(days=RUNS_RETENTION_DAYS))
        with self._db:
            self._db.execute(
                "INSERT INTO runs(topic_id, at, status, collected, new) VALUES (?, ?, ?, ?, ?)",
                (topic_id, _utc_stamp(at), status, collected, new),
            )
            self._db.execute("DELETE FROM runs WHERE at < ?", (cutoff,))

    def last_run(self, topic_id: str, statuses: Iterable[str]) -> datetime | None:
        """Time of the newest run of `topic_id` with one of `statuses`, or None."""
        wanted = list(statuses)
        if not wanted:
            return None
        marks = ",".join("?" * len(wanted))
        row = self._db.execute(
            f"SELECT MAX(at) FROM runs WHERE topic_id=? AND status IN ({marks})",  # noqa: S608
            (topic_id, *wanted),
        ).fetchone()
        return _parse_stamp(row[0]) if row and row[0] else None

    def record_source_failure(self, topic_id: str, source: str, at: datetime, error: str) -> None:
        cutoff = _utc_stamp(at - timedelta(days=RUNS_RETENTION_DAYS))
        with self._db:
            self._db.execute(
                "INSERT INTO source_failures(topic_id, source, at, error) VALUES (?, ?, ?, ?)",
                (topic_id, source[:100], _utc_stamp(at), error[:ERROR_TEXT_MAX]),
            )
            self._db.execute("DELETE FROM source_failures WHERE at < ?", (cutoff,))

    def source_failures_since(self, since: datetime) -> list[SourceFailure]:
        rows = self._db.execute(
            "SELECT topic_id, source, at, error FROM source_failures WHERE at >= ? ORDER BY id",
            (_utc_stamp(since),),
        ).fetchall()
        out: list[SourceFailure] = []
        for topic_id, source, at, error in rows:
            when = _parse_stamp(at)
            if when is not None:
                out.append(SourceFailure(str(topic_id), str(source), when, str(error)))
        return out

    # ---- Telegram subscribers
    def get_subscriber(self, chat_id: int) -> Subscriber | None:
        row = self._db.execute(
            "SELECT chat_id, topics, active FROM subscribers WHERE chat_id=?", (chat_id,)
        ).fetchone()
        if row is None:
            return None
        return Subscriber(int(row[0]), _decode_topics(row[1]), bool(row[2]))

    def subscribe(self, chat_id: int, topics: Iterable[str], now: datetime) -> None:
        """Create or replace a subscription and mark it active."""
        stamp = _utc_stamp(now)
        data = json.dumps(sorted(set(topics)))
        with self._db:
            self._db.execute(
                "INSERT INTO subscribers(chat_id, topics, active, created_at, updated_at) "
                "VALUES (?, ?, 1, ?, ?) ON CONFLICT(chat_id) DO UPDATE SET "
                "topics=excluded.topics, active=1, updated_at=excluded.updated_at",
                (chat_id, data, stamp, stamp),
            )

    def set_subscriber_topics(self, chat_id: int, topics: Iterable[str], now: datetime) -> bool:
        """Change topics of an existing subscriber without touching `active`."""
        with self._db:
            cur = self._db.execute(
                "UPDATE subscribers SET topics=?, updated_at=? WHERE chat_id=?",
                (json.dumps(sorted(set(topics))), _utc_stamp(now), chat_id),
            )
        return cur.rowcount > 0

    def deactivate_subscriber(self, chat_id: int, now: datetime) -> bool:
        """Mark inactive. Returns True when an active subscriber was deactivated."""
        with self._db:
            cur = self._db.execute(
                "UPDATE subscribers SET active=0, updated_at=? WHERE chat_id=? AND active=1",
                (_utc_stamp(now), chat_id),
            )
        return cur.rowcount > 0

    def _active_rows(self) -> list[tuple[int, tuple[str, ...]]]:
        rows = self._db.execute(
            "SELECT chat_id, topics FROM subscribers WHERE active=1 ORDER BY chat_id"
        ).fetchall()
        return [(int(r[0]), _decode_topics(r[1])) for r in rows]

    def active_subscribers(self, topic_id: str) -> list[int]:
        """Chat ids of active subscribers of `topic_id` (small table: filtered in Python)."""
        return [chat for chat, topics in self._active_rows() if topic_id in topics]

    def subscriber_counts(self) -> dict[str, int]:
        """Active subscribers per topic id."""
        counts: dict[str, int] = {}
        for _, topics in self._active_rows():
            for t in topics:
                counts[t] = counts.get(t, 0) + 1
        return counts

    def active_subscriber_count(self, *, exclude: int | None = None) -> int:
        row = self._db.execute(
            "SELECT COUNT(*) FROM subscribers WHERE active=1 AND chat_id IS NOT ?", (exclude,)
        ).fetchone()
        return int(row[0])

    # ---- bot state
    def load_bot_offset(self) -> int:
        value = self.load_snapshot(BOT_STATE_KEY).get("offset", 0)
        return value if isinstance(value, int) and not isinstance(value, bool) else 0

    def save_bot_offset(self, offset: int) -> None:
        self.save_snapshot(BOT_STATE_KEY, {"offset": int(offset)})
