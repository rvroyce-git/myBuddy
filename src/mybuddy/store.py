"""SQLite state: seen URLs (dedupe), source snapshots and saved reports."""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import sqlite3
from collections.abc import Iterable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

SEEN_RETENTION_DAYS = 30
REPORTS_PER_TOPIC = 30

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
"""


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
        except ValueError:
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
        data = json.loads(row[0])
        return data if isinstance(data, dict) else None

    def count_reports(self, topic_id: str) -> int:
        row = self._db.execute("SELECT COUNT(*) FROM reports WHERE topic_id=?", (topic_id,))
        return int(row.fetchone()[0])
