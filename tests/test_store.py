from __future__ import annotations

import stat
from datetime import timedelta
from pathlib import Path

from conftest import MONDAY

from mybuddy.store import REPORTS_PER_TOPIC, Store


def test_seen_dedupe_per_topic(data_dir: Path) -> None:
    with Store(data_dir) as store:
        urls = ["https://a.example/1", "https://a.example/2"]
        assert store.unseen("t", urls) == set(urls)
        store.mark_seen("t", urls[:1], MONDAY)
        assert store.unseen("t", urls) == {urls[1]}
        assert store.is_seen("t", "https://a.example/1/")  # trailing slash ignored
        assert not store.is_seen("other", urls[0])
        store.mark_seen("t", urls[:1], MONDAY)  # idempotent


def test_prune_older_than_30_days(data_dir: Path) -> None:
    with Store(data_dir) as store:
        store.mark_seen("t", ["https://old.example"], MONDAY - timedelta(days=31))
        store.mark_seen("t", ["https://new.example"], MONDAY - timedelta(days=29))
        assert store.prune_seen(MONDAY) == 1
        assert not store.is_seen("t", "https://old.example")
        assert store.is_seen("t", "https://new.example")


def test_snapshots_roundtrip(data_dir: Path) -> None:
    with Store(data_dir) as store:
        assert store.load_snapshot("k") == {}
        store.save_snapshot("k", {"a/b": 10})
        store.save_snapshot("k", {"a/b": 12})
        assert store.load_snapshot("k") == {"a/b": 12}


def test_reports_keep_last_30(data_dir: Path) -> None:
    with Store(data_dir) as store:
        assert store.latest_report("t") is None
        for i in range(REPORTS_PER_TOPIC + 5):
            store.save_report("t", MONDAY, {"headline": f"h{i}"})
        store.save_report("u", MONDAY, {"headline": "other"})
        assert store.count_reports("t") == REPORTS_PER_TOPIC
        assert store.latest_report("t") == {"headline": f"h{REPORTS_PER_TOPIC + 4}"}
        assert store.count_reports("u") == 1


def test_private_permissions(data_dir: Path) -> None:
    Store(data_dir).close()
    assert stat.S_IMODE(data_dir.stat().st_mode) == 0o700
    assert stat.S_IMODE((data_dir / "mybuddy.db").stat().st_mode) == 0o600
