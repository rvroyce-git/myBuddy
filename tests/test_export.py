from __future__ import annotations

import json
import os
import stat
from datetime import UTC, datetime
from pathlib import Path

import pytest
from conftest import MONDAY, make_topic

from mybuddy import export as export_mod
from mybuddy.config import WEEKDAYS, Schedule
from mybuddy.export import export, report_to_dict, schedule_label, write_json_atomic
from mybuddy.models import Report, ReportItem
from mybuddy.store import Store


@pytest.mark.parametrize(
    ("schedule", "label"),
    [
        (Schedule(time="06:30"), "Daily · 06:30 IST"),
        (Schedule(time="07:30", days=WEEKDAYS[:5]), "Mon–Fri · 07:30 IST"),
        (Schedule(time="10:00", days=WEEKDAYS[5:]), "Weekends · 10:00 IST"),
        (Schedule(time="09:00", days=("mon", "wed")), "Mon, Wed · 09:00 IST"),
        (Schedule(time="09:00", days=("tue", "wed", "thu")), "Tue–Thu · 09:00 IST"),
        (Schedule(every_minutes=60), "Every hour"),
        (Schedule(every_minutes=120), "Every 2 hours"),
        (Schedule(every_minutes=15), "Every 15 minutes"),
    ],
)
def test_schedule_label(schedule: Schedule, label: str) -> None:
    assert schedule_label(schedule) == label


def test_exact_shape_with_null_report(tmp_path: Path) -> None:
    topics = [
        make_topic(),
        make_topic(id="kev_alerts", title="KEV", emoji="🚨", schedule=Schedule(every_minutes=60)),
    ]
    report = Report(
        topic_id="ai_news",
        title="AI news",
        created_at=datetime(2026, 11, 23, 1, 0, 11, tzinfo=UTC),
        headline="H.",
        summary="S.",
        items=(
            ReportItem(
                title="T",
                url="https://x.example/1",
                source="Src",
                blurb="B.",
                published=datetime(2026, 11, 23, 1, 2, 3, tzinfo=UTC),
            ),
            ReportItem(title="U", url="https://x.example/2", source="Src", blurb=""),
        ),
    )
    path = tmp_path / "site" / "data" / "reports.json"
    with Store(tmp_path / "data") as store:
        stored = report_to_dict(report)
        stored["extra_field"] = "never exported"
        store.save_report("ai_news", report.created_at, stored)
        export(topics, store, path, MONDAY)
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data == {
        "generated_at": "2026-11-23T06:30:00+05:30",
        "topics": [
            {
                "id": "ai_news",
                "title": "AI news",
                "emoji": "🧠",
                "description": "AI things.",
                "schedule": "Daily · 06:30 IST",
                "report": {
                    "created_at": "2026-11-23T06:30:11+05:30",
                    "headline": "H.",
                    "summary": "S.",
                    "items": [
                        {
                            "title": "T",
                            "url": "https://x.example/1",
                            "source": "Src",
                            "blurb": "B.",
                            "published": "2026-11-23T01:02:03+00:00",
                        },
                        {
                            "title": "U",
                            "url": "https://x.example/2",
                            "source": "Src",
                            "blurb": "",
                            "published": None,
                        },
                    ],
                },
            },
            {
                "id": "kev_alerts",
                "title": "KEV",
                "emoji": "🚨",
                "description": "AI things.",
                "schedule": "Every hour",
                "report": None,
            },
        ],
    }
    assert "🧠" in path.read_text(encoding="utf-8")  # not ascii-escaped
    assert stat.S_IMODE(path.stat().st_mode) == 0o644


def test_atomic_write_keeps_old_file_on_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "reports.json"
    write_json_atomic(path, {"v": 1})

    def boom(src: str, dst: str) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(export_mod.os, "replace", boom)
    with pytest.raises(OSError):
        write_json_atomic(path, {"v": 2})
    assert json.loads(path.read_text(encoding="utf-8")) == {"v": 1}
    assert os.listdir(tmp_path) == ["reports.json"]  # temp file cleaned up


def test_non_ai_topics_are_not_website_widgets(tmp_path: Path) -> None:
    topics = [
        make_topic(),
        make_topic(
            id="kev_alerts", title="KEV", ai=False, prompt=None, schedule=Schedule(every_minutes=60)
        ),
    ]
    with Store(tmp_path / "data") as store:
        data = export(topics, store, tmp_path / "reports.json", MONDAY)
    assert [t["id"] for t in data["topics"]] == ["ai_news"]
