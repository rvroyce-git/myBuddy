"""Fake delivery: write formatted reports to the local outbox."""

from __future__ import annotations

from pathlib import Path

from mybuddy.deliver.base import split_message
from mybuddy.deliver.format import format_report
from mybuddy.models import Clock, Report
from mybuddy.store import ensure_private_dir


class OutboxWriter:
    """Fake delivery: write the formatted message to <data>/outbox/<topic>-<stamp>.md."""

    def __init__(self, data_dir: Path, clock: Clock) -> None:
        self.dir = data_dir / "outbox"
        self.clock = clock
        self.last_path: Path | None = None

    def deliver(self, report: Report, emoji: str) -> None:
        ensure_private_dir(self.dir)
        stamp = self.clock.now().strftime("%Y%m%d-%H%M%S")
        path = self.dir / f"{report.topic_id}-{stamp}.md"
        n = 1
        while path.exists():
            n += 1
            path = self.dir / f"{report.topic_id}-{stamp}-{n}.md"
        parts = split_message(format_report(report, emoji))
        path.write_text("\n\n".join(parts) + "\n", encoding="utf-8")
        self.last_path = path
