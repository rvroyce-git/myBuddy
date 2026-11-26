from __future__ import annotations

import os
import sqlite3
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


def test_subscriber_crud(data_dir: Path) -> None:
    with Store(data_dir) as store:
        assert store.get_subscriber(1) is None
        store.subscribe(1, ["security", "ai_news", "ai_news"], MONDAY)
        store.subscribe(2, ["ai_news"], MONDAY)
        store.subscribe(3, ["startups"], MONDAY)
        sub = store.get_subscriber(1)
        assert sub is not None and sub.active and sub.topics == ("ai_news", "security")
        assert store.active_subscribers("ai_news") == [1, 2]
        assert store.active_subscribers("security") == [1]
        assert store.active_subscribers("kev_alerts") == []
        assert store.subscriber_counts() == {"ai_news": 2, "security": 1, "startups": 1}
        assert store.active_subscriber_count() == 3
        assert store.active_subscriber_count(exclude=2) == 2

        assert store.set_subscriber_topics(2, ["startups"], MONDAY)
        assert not store.set_subscriber_topics(99, ["startups"], MONDAY)
        assert store.active_subscribers("startups") == [2, 3]

        assert store.deactivate_subscriber(3, MONDAY)
        assert not store.deactivate_subscriber(3, MONDAY)  # already inactive
        assert not store.deactivate_subscriber(99, MONDAY)
        assert store.active_subscribers("startups") == [2]
        assert store.active_subscriber_count() == 2
        inactive = store.get_subscriber(3)
        assert inactive is not None and not inactive.active

        store.subscribe(3, ["ai_models"], MONDAY + timedelta(days=1))  # comes back
        again = store.get_subscriber(3)
        assert again is not None and again.active and again.topics == ("ai_models",)
        row = store._db.execute(
            "SELECT created_at, updated_at FROM subscribers WHERE chat_id=3"
        ).fetchone()
        assert row[0] < row[1]  # created_at kept, updated_at moved


def test_bot_offset_roundtrip(data_dir: Path) -> None:
    with Store(data_dir) as store:
        assert store.load_bot_offset() == 0
        store.save_bot_offset(42)
    with Store(data_dir) as store:
        assert store.load_bot_offset() == 42
        store.save_snapshot("telegram:bot", {"offset": "junk"})
        assert store.load_bot_offset() == 0


def test_migrates_existing_database(data_dir: Path) -> None:
    """A production DB created before subscriptions keeps its data and gains the table."""
    data_dir.mkdir(mode=0o700)
    path = data_dir / "mybuddy.db"
    old = sqlite3.connect(path)
    old.executescript(
        """
        CREATE TABLE seen (topic_id TEXT NOT NULL, url_hash TEXT NOT NULL,
                           first_seen TEXT NOT NULL, PRIMARY KEY (topic_id, url_hash));
        CREATE TABLE snapshots (key TEXT PRIMARY KEY, json TEXT NOT NULL);
        CREATE TABLE reports (id INTEGER PRIMARY KEY AUTOINCREMENT, topic_id TEXT NOT NULL,
                              created_at TEXT NOT NULL, json TEXT NOT NULL);
        INSERT INTO reports(topic_id, created_at, json) VALUES ('t', 'x', '{"headline": "old"}');
        INSERT INTO snapshots(key, json) VALUES ('gh:repo', '{"a/b": 3}');
        """
    )
    old.commit()
    old.close()
    os.chmod(path, 0o600)
    for n in range(2):  # opening twice must be harmless
        with Store(data_dir) as store:
            assert store.latest_report("t") == {"headline": "old"}
            assert store.load_snapshot("gh:repo") == {"a/b": 3}
            assert store.active_subscriber_count() == n
            store.subscribe(7, ["ai_news"], MONDAY)
    with Store(data_dir) as store:
        assert store.active_subscribers("ai_news") == [7]
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
