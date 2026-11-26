"""mybuddy health: print the health check report."""

from __future__ import annotations

from collections.abc import Sequence

from mybuddy.config import Settings, Topic
from mybuddy.models import Clock
from mybuddy.store import Store


def cmd_health(topics: Sequence[Topic], settings: Settings, clock: Clock) -> int:
    """Print the health check. Exit 1 when a topic is stale; failed sources only warn."""
    from mybuddy.reports import collect_health, health_report, health_text

    health_topic = next((t for t in topics if t.kind == "health"), None)
    title = health_topic.title if health_topic else "Health check"
    now = clock.now()
    with Store(settings.data_dir) as store:
        health = collect_health(topics, store, settings, now)
    print(health_text(health_report("health", title, health, now)), end="")
    return 1 if health.stale else 0
