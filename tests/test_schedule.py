from __future__ import annotations

import io
import threading
from datetime import UTC, datetime, timedelta

import pytest
from conftest import FixedClock, make_topic

from mybuddy.config import WEEKDAYS, Schedule, Topic
from mybuddy.schedule import IST, Scheduler, next_run, weekday_key


def ist(day: int, hour: int, minute: int = 0) -> datetime:
    return datetime(2026, 11, day, hour, minute, tzinfo=IST)  # 23 Nov 2026 is a Monday


@pytest.mark.parametrize(
    ("schedule", "now", "expected"),
    [
        (Schedule(time="06:30"), ist(23, 5, 0), ist(23, 6, 30)),
        (Schedule(time="06:30"), ist(23, 6, 30), ist(24, 6, 30)),  # strictly after
        (Schedule(time="06:30"), ist(23, 23, 59), ist(24, 6, 30)),  # day rollover
        (Schedule(time="00:05"), ist(30, 23, 0), datetime(2026, 12, 1, 0, 5, tzinfo=IST)),
        (Schedule(time="07:30", days=WEEKDAYS[:5]), ist(27, 8, 0), ist(30, 7, 30)),  # Fri->Mon
        (Schedule(time="07:30", days=WEEKDAYS[:5]), ist(28, 6, 0), ist(30, 7, 30)),  # Sat
        (
            Schedule(time="09:00", days=("wed",)),
            ist(25, 9, 0),
            datetime(2026, 12, 2, 9, 0, tzinfo=IST),
        ),
        (Schedule(every_minutes=60), ist(23, 6, 30), ist(23, 7, 0)),
        (Schedule(every_minutes=60), ist(23, 7, 0), ist(23, 8, 0)),
        (Schedule(every_minutes=15), ist(23, 23, 50), ist(24, 0, 0)),
        (Schedule(every_minutes=7 * 60), ist(23, 22, 0), ist(24, 0, 0)),  # realign at midnight
    ],
)
def test_next_run(schedule: Schedule, now: datetime, expected: datetime) -> None:
    assert next_run(schedule, now) == expected


def test_next_run_converts_from_utc() -> None:
    now = datetime(2026, 11, 23, 0, 30, tzinfo=UTC)  # 06:00 IST
    result = next_run(Schedule(time="06:30"), now)
    assert result == ist(23, 6, 30) and result.utcoffset() == timedelta(hours=5, minutes=30)


def test_weekday_key_uses_ist() -> None:
    assert weekday_key(datetime(2026, 11, 22, 20, 0, tzinfo=UTC)) == "mon"  # 01:30 IST Monday


class SteppingClock(FixedClock):
    """Advances to the requested time whenever the scheduler waits."""


def test_scheduler_runs_in_order_and_survives_failures() -> None:
    clock = SteppingClock(ist(23, 6, 0))
    ran: list[str] = []
    topics: list[Topic] = [
        make_topic(id="late", schedule=Schedule(time="07:00")),
        make_topic(id="early", schedule=Schedule(time="06:30")),
    ]

    def run(topic: Topic) -> None:
        ran.append(topic.id)
        if topic.id == "early":
            raise RuntimeError("boom")

    class Stop(threading.Event):
        def wait(self, timeout: float | None = None) -> bool:
            clock.advance(seconds=timeout or 0)
            return False

    log = io.StringIO()
    sched = Scheduler(topics, run, clock, stop=Stop(), log=log, max_sleep=10_000)
    assert sched.loop(max_runs=3) == 3
    assert ran == ["early", "late", "early"]
    assert "early: run crashed: RuntimeError: boom" in log.getvalue()


def test_scheduler_stops_when_event_set() -> None:
    stop = threading.Event()
    stop.set()
    sched = Scheduler([make_topic()], lambda t: None, FixedClock(ist(23, 6, 0)), stop=stop)
    assert sched.loop() == 0
