"""mybuddy subscribers: counts per topic (never chat ids)."""

from __future__ import annotations

from collections.abc import Sequence

from mybuddy.config import Settings, Topic
from mybuddy.store import Store
from mybuddy.subscriptions import full_mask, topic_bits, website_topics


def cmd_subscribers(topics: Sequence[Topic], settings: Settings) -> int:
    """Counts only. Chat ids are personal data and are never printed."""
    with Store(settings.data_dir) as store:
        counts = store.subscriber_counts()
        active = store.active_subscriber_count()
    print(f"active subscribers: {active} (limit {settings.max_subscribers})")
    for t in website_topics(topics):
        extra = [f.id for f in topics if not f.on_website and f.audience == t.id]
        note = f"  (+ {', '.join(extra)})" if extra else ""
        print(f"  {t.emoji} {t.id:<16} {counts.get(t.id, 0):>5}{note}")
    mask = full_mask(topic_bits(topics))
    if mask:
        print(f"start link (all topics): https://t.me/{settings.bot_username}?start=t{mask:x}")
    return 0
