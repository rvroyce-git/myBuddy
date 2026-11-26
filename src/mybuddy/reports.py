"""Reports built from MyBuddy's own state instead of fetching: the morning digest and the
weekly review (from stored reports, summarised by the AI) and the health check (no AI).

All three are Telegram-only (`website: false`). The health check goes to the owner only:
config validation forbids it an `audience`, so subscriptions.audience_map never routes it.
"""

from __future__ import annotations

import os
import shutil
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

from mybuddy.config import WEEKDAYS, Schedule, Settings, Topic
from mybuddy.models import Item, Report
from mybuddy.schedule import IST
from mybuddy.sources._common import clip, parse_iso
from mybuddy.store import Store, StoredReport
from mybuddy.summarize import MAX_AI_ITEMS, SNIPPET_CHARS

DIGEST_ITEMS_PER_TOPIC = 2  # top items taken from each of today's reports
WEEKLY_DAYS = 7
WEEKLY_ITEMS_PER_REPORT = 1  # each report's top item, with the report headline as context
STALE_HOURS = 26  # a daily topic with no successful run for this long is stale
OK_STATUSES = ("delivered", "no_new_items")


def source_topics(topics: Sequence[Topic], exclude: str) -> list[Topic]:
    """Topics whose reports feed the digest and the weekly review (news topics only)."""
    return [t for t in topics if t.kind == "news" and t.id != exclude]


def _url_key(url: str) -> str:
    """URL identity for de-duplication: ignore the fragment, a trailing slash and case."""
    return url.split("#", 1)[0].strip().rstrip("/").lower()


def _round_robin(groups: list[list[Item]], limit: int, per_group: int | None = None) -> list[Item]:
    """Interleave groups (one item per group per round), skipping URLs already taken; a
    group whose next item is a duplicate falls through to its next candidate, up to
    ``per_group`` picks each."""
    out: list[Item] = []
    seen: set[str] = set()
    cursors = [0] * len(groups)
    taken = [0] * len(groups)
    cap = per_group if per_group is not None else max((len(g) for g in groups), default=0)
    progress = True
    while progress and len(out) < limit:
        progress = False
        for g, group in enumerate(groups):
            if taken[g] >= cap or len(out) >= limit:
                continue
            while cursors[g] < len(group):
                item = group[cursors[g]]
                cursors[g] += 1
                if _url_key(item.url) not in seen:
                    seen.add(_url_key(item.url))
                    out.append(item)
                    taken[g] += 1
                    progress = True
                    break
    return out


def _items_of(report: StoredReport, topic: Topic, count: int, context: str = "") -> list[Item]:
    raw = report.data.get("items")
    out: list[Item] = []
    for entry in raw if isinstance(raw, list) else []:
        if len(out) >= count:
            break
        if not isinstance(entry, dict):
            continue
        title, url = entry.get("title"), entry.get("url")
        if not isinstance(title, str) or not isinstance(url, str) or not title.strip():
            continue
        source = entry.get("source") if isinstance(entry.get("source"), str) else ""
        blurb = entry.get("blurb") if isinstance(entry.get("blurb"), str) else ""
        snippet = f"{context} — {blurb}" if context and blurb else context or str(blurb)
        out.append(
            Item(
                title=title,
                url=url,
                source=clip(f"{topic.title} · {source}" if source else topic.title, 80),
                published=parse_iso(entry.get("published")),
                snippet=clip(snippet, SNIPPET_CHARS),
                extra={"topic": topic.id},
            )
        )
    return out


def digest_items(topics: Sequence[Topic], store: Store, now: datetime, self_id: str) -> list[Item]:
    """Top items of today's (IST) latest report of each news topic, interleaved by topic."""
    midnight = now.astimezone(IST).replace(hour=0, minute=0, second=0, microsecond=0)
    latest: dict[str, StoredReport] = {}
    for report in store.reports_since(midnight):
        current = latest.get(report.topic_id)
        if current is None or report.created_at >= current.created_at:
            latest[report.topic_id] = report
    # Fetch every candidate so a topic whose top item duplicates another topic's can fall
    # through to its next-best item instead of repeating the same link in the digest.
    groups = [
        _items_of(latest[t.id], t, MAX_AI_ITEMS)
        for t in source_topics(topics, self_id)
        if t.id in latest
    ]
    return _round_robin(groups, MAX_AI_ITEMS, DIGEST_ITEMS_PER_TOPIC)


def weekly_items(topics: Sequence[Topic], store: Store, now: datetime, self_id: str) -> list[Item]:
    """Each report's top item from the last 7 days (newest first per topic), interleaved."""
    by_topic: dict[str, list[StoredReport]] = {}
    for report in store.reports_since(now - timedelta(days=WEEKLY_DAYS)):
        by_topic.setdefault(report.topic_id, []).append(report)
    groups: list[list[Item]] = []
    for topic in source_topics(topics, self_id):
        items: list[Item] = []
        newest_first = sorted(by_topic.get(topic.id, []), key=lambda r: r.created_at)[::-1]
        for report in newest_first:
            headline = report.data.get("headline")
            context = headline if isinstance(headline, str) else ""
            items += _items_of(report, topic, WEEKLY_ITEMS_PER_REPORT, context)
        if items:
            groups.append(items)
    return _round_robin(groups, MAX_AI_ITEMS)


# --------------------------------------------------------------------------- health check


def stale_after(schedule: Schedule) -> timedelta:
    """How long without a successful run before a topic counts as stale: 26 h for daily and
    interval topics, plus the longest gap between run days for the others (weekday-only or
    weekly topics are not stale over a weekend)."""
    if schedule.every_minutes or schedule.days == WEEKDAYS:
        return timedelta(hours=STALE_HOURS)
    idx = sorted(WEEKDAYS.index(d) for d in schedule.days)
    gaps = [(idx[(i + 1) % len(idx)] - d) % 7 or 7 for i, d in enumerate(idx)]
    return timedelta(days=max(gaps) - 1, hours=STALE_HOURS)


@dataclass(frozen=True)
class TopicHealth:
    topic: Topic
    last_ok: datetime | None
    reports: int
    stale: bool


@dataclass(frozen=True)
class Health:
    topics: list[TopicHealth]
    failures: dict[tuple[str, str], tuple[int, str]]  # (topic, source) -> (count, last error)
    total_reports: int
    disk_free: int | None
    disk_total: int | None
    memory_bytes: int | None
    groq: bool
    telegram: bool

    @property
    def stale(self) -> list[TopicHealth]:
        return [t for t in self.topics if t.stale]


def _last_ok(store: Store, topic_id: str) -> datetime | None:
    """Newest successful run; the latest saved report counts too (older installs had no
    run log)."""
    candidates = [store.last_run(topic_id, OK_STATUSES)]
    latest = store.latest_report(topic_id)
    if latest:
        candidates.append(parse_iso(latest.get("created_at")))
    found = [c for c in candidates if c is not None]
    return max(found) if found else None


def process_memory() -> int | None:
    """Current resident memory in bytes (Linux /proc), else the peak from getrusage."""
    try:
        with open("/proc/self/status", encoding="ascii") as fh:
            for line in fh:
                if line.startswith("VmRSS:"):
                    return int(line.split()[1]) * 1024
    except (OSError, ValueError, IndexError):
        pass
    try:
        import resource

        peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    except (ImportError, OSError, ValueError):
        return None
    return int(peak) if sys.platform == "darwin" else int(peak) * 1024  # macOS: bytes


def collect_health(
    topics: Sequence[Topic],
    store: Store,
    settings: Settings,
    now: datetime,
    *,
    self_id: str = "",
    data_dir: Path | None = None,
) -> Health:
    counts = store.report_counts()
    rows: list[TopicHealth] = []
    for topic in topics:
        if topic.kind == "health" or topic.id == self_id:
            continue
        last = _last_ok(store, topic.id)
        stale = last is not None and now - last > stale_after(topic.schedule)
        rows.append(TopicHealth(topic, last, counts.get(topic.id, 0), stale))
    failures: dict[tuple[str, str], tuple[int, str]] = {}
    for f in store.source_failures_since(now - timedelta(hours=24)):
        n, _ = failures.get((f.topic_id, f.source), (0, ""))
        failures[(f.topic_id, f.source)] = (n + 1, f.error)
    disk_free = disk_total = None
    try:
        usage = shutil.disk_usage(data_dir or settings.data_dir)
        disk_free, disk_total = usage.free, usage.total
    except OSError:
        pass
    return Health(
        topics=rows,
        failures=failures,
        total_reports=sum(counts.values()),
        disk_free=disk_free,
        disk_total=disk_total,
        memory_bytes=process_memory(),
        groq=bool(settings.groq_api_key),
        telegram=bool(settings.telegram_bot_token and settings.telegram_chat_id),
    )


def _size(n: int) -> str:
    value = float(n)
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024 or unit == "GB":
            return f"{value:.0f} {unit}" if unit in ("B", "KB") else f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.1f} GB"  # pragma: no cover


def _age(delta: timedelta) -> str:
    hours = delta.total_seconds() / 3600
    if hours < 1:
        return f"{max(0, int(delta.total_seconds() // 60))} min ago"
    if hours < 48:
        return f"{hours:.0f} h ago"
    return f"{hours / 24:.0f} days ago"


def health_lines(health: Health, now: datetime) -> list[str]:
    lines = ["Topics (last successful run):"]
    for row in health.topics:
        t = row.topic
        if row.last_ok is None:
            lines.append(f"• {t.id}: never run yet · {row.reports} reports")
            continue
        when = row.last_ok.astimezone(IST)
        mark = "⚠️ STALE" if row.stale else "✅"
        lines.append(
            f"{mark} {t.id}: {when:%d %b %H:%M} IST ({_age(now - row.last_ok)}) · "
            f"{row.reports} reports"
        )
    if health.failures:
        lines += ["", f"Failed sources, last 24 h: {len(health.failures)}"]
        for (topic_id, source), (count, error) in sorted(health.failures.items()):
            times = f" ×{count}" if count > 1 else ""
            lines.append(f"⚠️ {topic_id} / {source}{times}: {clip(error, 120)}")
    else:
        lines += ["", "Failed sources, last 24 h: none"]
    lines += ["", f"Reports stored: {health.total_reports}"]
    if health.disk_free is not None and health.disk_total:
        pct = 100 * health.disk_free / health.disk_total
        lines.append(
            f"Disk free (data dir): {_size(health.disk_free)} of {_size(health.disk_total)} "
            f"({pct:.0f}%)"
        )
    if health.memory_bytes is not None:
        lines.append(f"Process memory: {_size(health.memory_bytes)} (pid {os.getpid()})")
    lines.append(f"Groq: {'configured' if health.groq else 'NOT configured'}")
    lines.append(f"Telegram: {'configured' if health.telegram else 'NOT configured'}")
    return lines


def health_report(topic_id: str, title: str, health: Health, now: datetime) -> Report:
    problems: list[str] = []
    if health.stale:
        problems.append(f"{len(health.stale)} stale: {', '.join(t.topic.id for t in health.stale)}")
    if health.failures:
        problems.append(f"{len(health.failures)} failing source(s)")
    if not health.groq:
        problems.append("no Groq key")
    headline = "⚠️ " + "; ".join(problems) if problems else "✅ All good"
    return Report(
        topic_id=topic_id,
        title=title,
        created_at=now,
        headline=clip(headline, 200),
        summary="\n".join(health_lines(health, now)),
        items=(),
    )


def health_text(report: Report) -> str:
    """Plain text for `mybuddy health`."""
    return f"{report.title}\n{report.headline}\n\n{report.summary}\n"
