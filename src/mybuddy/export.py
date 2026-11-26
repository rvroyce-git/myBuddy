"""Write site/data/reports.json (the website data contract) atomically."""

from __future__ import annotations

import contextlib
import json
import os
import tempfile
from collections.abc import Sequence
from datetime import datetime
from pathlib import Path
from typing import Any

from mybuddy.config import WEEKDAYS, Schedule, Topic
from mybuddy.models import Report
from mybuddy.schedule import IST
from mybuddy.store import Store

_DAY_LABELS = dict(zip(WEEKDAYS, ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"), strict=True))


def schedule_label(schedule: Schedule) -> str:
    if schedule.every_minutes:
        m = schedule.every_minutes
        if m == 60:
            return "Every hour"
        if m % 60 == 0:
            return f"Every {m // 60} hours"
        return f"Every {m} minutes"
    days = schedule.days
    if days == WEEKDAYS:
        label = "Daily"
    elif days == WEEKDAYS[:5]:
        label = "Mon–Fri"
    elif days == WEEKDAYS[5:]:
        label = "Weekends"
    else:
        idx = [WEEKDAYS.index(d) for d in days]
        contiguous = len(idx) > 2 and idx == list(range(idx[0], idx[-1] + 1))
        if contiguous:
            label = f"{_DAY_LABELS[days[0]]}–{_DAY_LABELS[days[-1]]}"
        else:
            label = ", ".join(_DAY_LABELS[d] for d in days)
    return f"{label} · {schedule.time} IST"


def _iso(when: datetime | None) -> str | None:
    return when.isoformat(timespec="seconds") if when else None


def report_to_dict(report: Report) -> dict[str, Any]:
    """The `report` object of the website contract."""
    return {
        "created_at": report.created_at.astimezone(IST).isoformat(timespec="seconds"),
        "headline": report.headline,
        "summary": report.summary,
        "items": [
            {
                "title": i.title,
                "url": i.url,
                "source": i.source,
                "blurb": i.blurb,
                "published": _iso(i.published),
            }
            for i in report.items
        ],
    }


_REPORT_KEYS = ("created_at", "headline", "summary", "items")
_ITEM_KEYS = ("title", "url", "source", "blurb", "published")


def _contract_report(data: dict[str, Any] | None) -> dict[str, Any] | None:
    """Project a stored report onto exactly the contract keys."""
    if not data:
        return None
    out = {k: data.get(k) for k in _REPORT_KEYS}
    items = data.get("items")
    out["items"] = [
        {k: item.get(k) for k in _ITEM_KEYS}
        for item in (items if isinstance(items, list) else [])
        if isinstance(item, dict)
    ]
    return out


def build_export(topics: Sequence[Topic], store: Store, now: datetime) -> dict[str, Any]:
    return {
        "generated_at": now.astimezone(IST).isoformat(timespec="seconds"),
        "topics": [
            {
                "id": t.id,
                "title": t.title,
                "emoji": t.emoji,
                "description": t.description,
                "schedule": schedule_label(t.schedule),
                # Permanent Telegram start-link bit (subscriptions contract v2).
                "bit": t.bit,
                "report": _contract_report(store.latest_report(t.id)),
            }
            # Non-AI topics (the hourly KEV alert) and `website: false` topics (digest,
            # health check, policy watch ...) are Telegram/Discord-only, not website widgets.
            for t in topics
            if t.on_website
        ],
    }


def write_json_atomic(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False, indent=2)
            fh.write("\n")
            fh.flush()
            os.fsync(fh.fileno())
        os.chmod(tmp, 0o644)  # public website data, readable by the web server
        os.replace(tmp, path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise


def export(topics: Sequence[Topic], store: Store, path: Path, now: datetime) -> dict[str, Any]:
    data = build_export(topics, store, now)
    write_json_atomic(path, data)
    return data
