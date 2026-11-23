"""Run one topic end to end: collect -> dedupe -> summarise -> deliver -> save -> export."""

from __future__ import annotations

import re
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, TextIO
from urllib.parse import urlsplit

from mybuddy.config import Topic
from mybuddy.deliver import Deliverer, format_report
from mybuddy.export import export, report_to_dict
from mybuddy.models import Clock, HttpClient, Item, Report, Source
from mybuddy.schedule import weekday_key
from mybuddy.store import Store, url_hash
from mybuddy.summarize import Summarizer, plain_report

SourceBuilder = Callable[[dict[str, Any], HttpClient, Clock], Source]

STATEFUL_SOURCE_TYPES = frozenset({"github_trending"})
MAX_URL_LENGTH = 2048
_URL_BAD_CHARS = re.compile(r"[\s\x00-\x1f\x7f]")


def deliverable_url(url: object) -> bool:
    """True for a short, clean https URL with a host. Anything else could break a message
    (a newline inside a Telegram link, a line too long to send) and block the topic."""
    if not isinstance(url, str) or len(url) > MAX_URL_LENGTH or not url.startswith("https://"):
        return False
    if _URL_BAD_CHARS.search(url):
        return False
    try:
        return bool(urlsplit(url).hostname)
    except ValueError:
        return False


def _usable(item: object) -> bool:
    return (
        isinstance(item, Item)
        and isinstance(item.title, str)
        and isinstance(item.source, str)
        and isinstance(item.snippet, str)
        and deliverable_url(item.url)
    )


def _default_build_source(spec: dict[str, Any], http: HttpClient, clock: Clock) -> Source:
    from mybuddy.sources import build_source

    return build_source(spec, http, clock)


@dataclass
class Deps:
    http: HttpClient
    clock: Clock
    store: Store
    summarizer: Summarizer
    deliverer: Deliverer
    topics: Sequence[Topic] = ()  # all topics, for export
    export_path: Path | None = None
    build_source: SourceBuilder = _default_build_source
    log: TextIO | None = None
    out: TextIO | None = None  # dry-run output


@dataclass
class RunResult:
    topic_id: str
    status: str  # delivered | no_new_items | skipped | dry_run | failed
    report: Report | None = None
    collected: int = 0
    new: int = 0
    errors: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.status != "failed"


def _log(deps: Deps, msg: str) -> None:
    print(f"[mybuddy] {msg}", file=deps.log or sys.stderr, flush=True)


def snapshot_key(topic_id: str, source_name: str) -> str:
    return f"{topic_id}:{source_name}"


def resolve_run(topic: Topic, deps: Deps) -> tuple[str, tuple[dict[str, Any], ...]] | None:
    """Title and source specs for this run, or None when the rotation has no entry today."""
    if not topic.rotation:
        return topic.title, topic.sources
    entry = topic.rotation.get(weekday_key(deps.clock.now()))
    if entry is None:
        return None
    return f"{topic.title}: {entry.title}", entry.sources


def _interleave(groups: list[list[Item]]) -> list[Item]:
    """Round-robin merge so one busy source doesn't crowd out the others."""
    out: list[Item] = []
    longest = max((len(g) for g in groups), default=0)
    for i in range(longest):
        out.extend(g[i] for g in groups if i < len(g))
    return out


def collect(
    topic: Topic,
    specs: Sequence[dict[str, Any]],
    deps: Deps,
    *,
    persist_state: bool,
) -> tuple[list[Item], dict[str, dict[str, Any]], list[str]]:
    """Collect from each source in isolation. Returns (items, pending snapshots, errors)."""
    groups: list[list[Item]] = []
    snapshots: dict[str, dict[str, Any]] = {}
    errors: list[str] = []
    for spec in specs:
        name = str(spec.get("name", spec.get("type", "?")))
        spec = dict(spec)
        if spec.get("type") in STATEFUL_SOURCE_TYPES:
            key = snapshot_key(topic.id, name)
            state = deps.store.load_snapshot(key)
            spec["_state"] = state
            if persist_state:
                snapshots[key] = state
        try:
            source = deps.build_source(spec, deps.http, deps.clock)
            items = [i for i in source.collect() if _usable(i)]
        except Exception as exc:
            msg = f"{topic.id}: source {name!r} failed: {type(exc).__name__}: {exc}"
            _log(deps, msg)
            errors.append(msg)
            snapshots.pop(snapshot_key(topic.id, name), None)
            continue
        groups.append(items)
    merged: list[Item] = []
    seen_hashes: set[str] = set()
    for item in _interleave(groups):
        h = url_hash(item.url)
        if h not in seen_hashes:
            seen_hashes.add(h)
            merged.append(item)
    return merged, snapshots, errors


def run_topic(topic: Topic, deps: Deps, *, dry_run: bool = False) -> RunResult:
    resolved = resolve_run(topic, deps)
    if resolved is None:
        _log(deps, f"{topic.id}: no rotation entry for {weekday_key(deps.clock.now())}; skipping")
        return RunResult(topic.id, "skipped")
    title, specs = resolved

    items, snapshots, errors = collect(topic, specs, deps, persist_state=not dry_run)
    fresh_urls = deps.store.unseen(topic.id, [i.url for i in items])
    fresh = [i for i in items if i.url in fresh_urls]
    result = RunResult(
        topic.id, "no_new_items", collected=len(items), new=len(fresh), errors=errors
    )

    if not fresh:
        _log(deps, f"{topic.id}: no new items ({len(items)} collected, all seen)")
        if not dry_run:
            for key, state in snapshots.items():
                deps.store.save_snapshot(key, state)
        if errors and not items:
            result.status = "failed"
        return result

    shown = fresh[: topic.max_items]
    if topic.ai:
        report = deps.summarizer.summarize(
            topic, shown, title=title, fake=True if dry_run else None
        )
    else:
        report = plain_report(topic.id, title, shown, deps.clock)
    result.report = report

    if dry_run:
        print(format_report(report, topic.emoji), file=deps.out or sys.stdout, flush=True)
        result.status = "dry_run"
        return result

    try:
        deps.deliverer.deliver(report, topic.emoji)
    except Exception as exc:
        msg = f"{topic.id}: delivery failed: {type(exc).__name__}: {exc}"
        _log(deps, msg)
        result.errors.append(msg)
        result.status = "failed"
        return result

    now = deps.clock.now()
    # Mark every fresh item seen (not only those shown) so nothing goes stale tomorrow.
    deps.store.mark_seen(topic.id, [i.url for i in fresh], now)
    deps.store.prune_seen(now)
    for key, state in snapshots.items():
        deps.store.save_snapshot(key, state)
    deps.store.save_report(topic.id, report.created_at, report_to_dict(report))
    result.status = "delivered"
    _log(deps, f"{topic.id}: delivered {len(report.items)} items")

    if deps.export_path is not None and deps.topics:
        try:
            export(deps.topics, deps.store, deps.export_path, now)
        except Exception as exc:  # the website is secondary; never fail the run for it
            _log(deps, f"export failed: {type(exc).__name__}: {exc}")
    return result
