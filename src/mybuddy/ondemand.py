"""Reports on demand from the bot: run a topic now (owner) or show its latest report (anyone).

* Topic matching is deterministic (regexes over topic ids, titles and a few aliases); no AI
  decides what runs.
* `run_lock` is a file lock shared by the scheduler (`mybuddy serve`) and on-demand runs, so
  two runs never overlap. On-demand runs happen in one background thread at a time, so the
  bot keeps polling meanwhile.
* An on-demand run delivers to the owner channel only (no subscriber fan-out).
"""

from __future__ import annotations

import fcntl
import re
import threading
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any

from mybuddy.config import Topic
from mybuddy.deliver.format import format_telegram
from mybuddy.models import Report, ReportItem

RUN_LOCK_FILE = "run.lock"

# Extra words people use for a topic; only used for topics that exist in the config.
ALIASES: dict[str, tuple[str, ...]] = {
    "ai_news": ("ai news", "ai"),
    "security": ("security", "cybersecurity", "cyber security", "security alerts"),
    "kev_alerts": ("kev", "kev alerts", "exploited vulnerabilities"),
    "github_trending": ("github trending", "trending github", "github", "trending repos"),
    "deep_dive": ("deep dive",),
    "research_papers": ("research papers", "papers", "research"),
    "startups": ("startups", "startup", "launches"),
    "ai_models": ("ai models", "models", "open models"),
    "morning_digest": ("morning digest", "digest"),
    "health_check": ("health check", "health"),
    "policy_watch": ("policy watch", "policy", "tech policy"),
    "product_recalls": ("product recalls", "recalls"),
    "industry_signals": ("industry signals", "signals"),
    "weekly_review": ("weekly review", "weekly"),
}

_RUN_RE = re.compile(
    r"^(?:please\s+|pls\s+)?(?:run|send\s+me|refresh|re-?run|fetch)\b", re.IGNORECASE
)
_LATEST_RE = re.compile(
    r"^(?:please\s+|pls\s+)?(?:show|give|send)\s+me\s+(?:today'?s|todays|the\s+latest|latest)\b",
    re.IGNORECASE,
)


def _norm(text: str) -> str:
    return " ".join(re.sub(r"[^a-z0-9]+", " ", text.lower().replace("’", "'")).split())


def _aliases(topic: Topic) -> set[str]:
    names = {_norm(topic.id), _norm(topic.title), *map(_norm, ALIASES.get(topic.id, ()))}
    return {n for n in names if n}


def find_topic(text: str, topics: Sequence[Topic]) -> Topic | None:
    """The topic whose id/title/alias appears in `text` (the longest match wins)."""
    hay = f" {_norm(text)} "
    best: tuple[int, Topic] | None = None
    for topic in topics:
        for alias in _aliases(topic):
            if f" {alias} " in hay and (best is None or len(alias) > best[0]):
                best = (len(alias), topic)
    return best[1] if best else None


def run_request(text: str, topics: Sequence[Topic]) -> Topic | None:
    """'send me the security report now', 'run github trending' -> that topic."""
    if not _RUN_RE.match(text.strip()) or _LATEST_RE.match(text.strip()):
        return None
    return find_topic(text, topics)


def latest_request(text: str, topics: Sequence[Topic]) -> Topic | None:
    """'show me today's security' -> that topic."""
    return find_topic(text, topics) if _LATEST_RE.match(text.strip()) else None


def _when(value: Any) -> datetime:
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value)
        except ValueError:
            pass
    return datetime.fromtimestamp(0).astimezone()


def _text(value: Any) -> str:
    return value if isinstance(value, str) else ""


def stored_report(topic: Topic, data: dict[str, Any]) -> Report:
    raw = data.get("items")
    items = tuple(
        ReportItem(
            title=_text(i.get("title")),
            url=_text(i.get("url")),
            source=_text(i.get("source")),
            blurb=_text(i.get("blurb")),
        )
        for i in (raw if isinstance(raw, list) else [])
        if isinstance(i, dict) and _text(i.get("url")).startswith("https://") and i.get("title")
    )
    return Report(
        topic_id=topic.id,
        title=topic.title,
        created_at=_when(data.get("created_at")),
        headline=_text(data.get("headline")),
        summary=_text(data.get("summary")),
        items=items,
    )


def format_latest(topic: Topic, data: dict[str, Any]) -> str:
    """Telegram HTML, exactly like the scheduled message."""
    return format_telegram(stored_report(topic, data), topic.emoji)


@contextmanager
def run_lock(data_dir: Path) -> Iterator[None]:
    """Exclusive file lock shared by scheduled and on-demand runs (blocks until free)."""
    data_dir.mkdir(parents=True, exist_ok=True)
    with open(data_dir / RUN_LOCK_FILE, "a") as fh:
        fcntl.flock(fh, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(fh, fcntl.LOCK_UN)


class OnDemandRunner:
    """Runs one topic at a time in a background thread, under the shared run lock.

    `run(topic)` returns the reply text ("Done ..." or the error). `start` returns False
    when a run is already in progress.
    """

    def __init__(self, run: Callable[[Topic], str], data_dir: Path) -> None:
        self._run = run
        self.data_dir = data_dir
        self._busy = threading.Lock()
        self.thread: threading.Thread | None = None

    def start(self, topic: Topic, done: Callable[[str], None]) -> bool:
        if not self._busy.acquire(blocking=False):
            return False

        def work() -> None:
            try:
                with run_lock(self.data_dir):
                    reply = self._run(topic)
            except Exception as exc:
                reply = f"{topic.title}: failed ({type(exc).__name__})."
            finally:
                self._busy.release()
            done(reply)

        self.thread = threading.Thread(target=work, name=f"run-{topic.id}", daemon=True)
        self.thread.start()
        return True
