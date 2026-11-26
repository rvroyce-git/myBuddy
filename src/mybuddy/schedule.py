"""A tiny in-process scheduler: compute next run times in IST and sleep until then."""

from __future__ import annotations

import gc
import signal
import sys
import threading
from collections.abc import Callable, Sequence
from datetime import datetime, timedelta, timezone, tzinfo
from types import FrameType
from typing import TextIO

from mybuddy.config import WEEKDAYS, Schedule, Topic
from mybuddy.models import Clock


def _load_ist() -> tzinfo:
    try:
        from zoneinfo import ZoneInfo

        return ZoneInfo("Asia/Kolkata")
    except Exception:
        # No system tz database and no tzdata package. India has no DST, so a fixed
        # +05:30 offset is exactly equivalent.
        return timezone(timedelta(hours=5, minutes=30), "IST")


IST: tzinfo = _load_ist()
MAX_SLEEP_SECONDS = 300.0
RELOAD_POLL_SECONDS = 60.0


class SystemClock:
    def now(self) -> datetime:
        return datetime.now(IST)


def weekday_key(when: datetime) -> str:
    """'mon'..'sun' for the IST date of `when`."""
    return WEEKDAYS[when.astimezone(IST).weekday()]


def next_run(schedule: Schedule, now: datetime) -> datetime:
    """The first scheduled time strictly after `now`, as an IST-aware datetime."""
    local = now.astimezone(IST)
    if schedule.every_minutes:
        step = schedule.every_minutes
        midnight = local.replace(hour=0, minute=0, second=0, microsecond=0)
        elapsed = (local - midnight).total_seconds() / 60
        k = int(elapsed // step) + 1
        candidate = midnight + timedelta(minutes=k * step)
        # Alignment restarts at midnight when step does not divide a day evenly.
        return min(candidate, midnight + timedelta(days=1))
    for offset in range(8):
        day = (local + timedelta(days=offset)).date()
        candidate = datetime(
            day.year, day.month, day.day, schedule.hour, schedule.minute, tzinfo=IST
        )
        if candidate > local and WEEKDAYS[candidate.weekday()] in schedule.days:
            return candidate
    raise ValueError("schedule has no valid days")  # pragma: no cover - config validates


class Scheduler:
    """Runs each topic at its next due time. One topic failing never stops the loop."""

    def __init__(
        self,
        topics: Sequence[Topic],
        run: Callable[[Topic], object],
        clock: Clock,
        *,
        stop: threading.Event | None = None,
        log: TextIO | None = None,
        max_sleep: float = MAX_SLEEP_SECONDS,
        reload: Callable[[], Sequence[Topic] | None] | None = None,
    ) -> None:
        self.topics = list(topics)
        self.run = run
        self.clock = clock
        self.stop = stop or threading.Event()
        self.log = log
        # With `reload`, the loop wakes at least once a minute and calls it; a non-None result
        # (new topics after an approved config change) replaces the schedule.
        self.reload = reload
        self.max_sleep = min(max_sleep, RELOAD_POLL_SECONDS) if reload else max_sleep
        self.due: dict[str, datetime] = {}
        self.by_id: dict[str, Topic] = {}

    def _log(self, msg: str) -> None:
        print(f"[mybuddy] {msg}", file=self.log or sys.stderr, flush=True)

    def install_signal_handlers(self) -> None:
        def handler(signum: int, _frame: FrameType | None) -> None:
            self._log(f"received signal {signum}; stopping after the current task")
            self.stop.set()

        signal.signal(signal.SIGTERM, handler)
        signal.signal(signal.SIGINT, handler)

    def _plan(self) -> None:
        """Next run of every enabled topic; disabled topics are skipped."""
        now = self.clock.now()
        self.by_id = {t.id: t for t in self.topics if t.enabled}
        self.due = {tid: next_run(t.schedule, now) for tid, t in self.by_id.items()}
        for t in self.topics:
            if t.id in self.due:
                self._log(f"{t.id}: next run {self.due[t.id]:%a %d %b %H:%M} IST")
            else:
                self._log(f"{t.id}: disabled")

    def _check_reload(self) -> None:
        if self.reload is None:
            return
        try:
            topics = self.reload()
        except Exception as exc:  # a bad reload keeps the current schedule
            self._log(f"config reload failed: {type(exc).__name__}: {exc}")
            return
        if topics is not None:
            self.topics = list(topics)
            self._log(f"config reloaded: {len(self.topics)} topics")
            self._plan()

    def loop(self, max_runs: int | None = None) -> int:
        """Sleep/run until stopped. Returns the number of topic runs performed."""
        self._plan()
        due, by_id = self.due, self.by_id
        runs = 0
        while not self.stop.is_set():
            self._check_reload()
            due, by_id = self.due, self.by_id
            if not due:
                if self.reload is None:
                    break
                self.stop.wait(self.max_sleep)  # nothing enabled: just watch for a reload
                continue
            topic_id = min(due, key=lambda k: due[k])
            wait = (due[topic_id] - self.clock.now()).total_seconds()
            if wait > 0:
                # Wake at least every max_sleep to tolerate clock jumps / suspend.
                self.stop.wait(min(wait, self.max_sleep))
                continue
            topic = by_id[topic_id]
            try:
                self.run(topic)
            except Exception as exc:
                self._log(f"{topic_id}: run crashed: {type(exc).__name__}: {exc}")
            runs += 1
            due[topic_id] = next_run(topic.schedule, max(self.clock.now(), due[topic_id]))
            self._log(f"{topic_id}: next run {due[topic_id]:%a %d %b %H:%M} IST")
            gc.collect()
            if max_runs is not None and runs >= max_runs:
                break
        return runs
