"""mybuddy list | run | serve | export."""

from __future__ import annotations

import contextlib
import sqlite3
import sys
from collections.abc import Collection, Sequence

from mybuddy.cli._common import _err, new_http
from mybuddy.config import Settings, Topic
from mybuddy.deliver import make_deliverer
from mybuddy.engine import Deps, RunResult, run_topic
from mybuddy.export import export, schedule_label
from mybuddy.models import Clock
from mybuddy.schedule import Scheduler, next_run
from mybuddy.store import Store
from mybuddy.summarize import Summarizer

LAST_RUN_KEY = "_run:{}"  # snapshot key per topic ('_' never starts a topic id); see doctor


def record_run(store: Store, topic_id: str, status: str, clock: Clock) -> None:
    """Remember when a topic last ran (even with nothing new) so `mybuddy doctor` can spot
    a stalled scheduler. Best effort: bookkeeping never fails a run."""
    with contextlib.suppress(sqlite3.Error, OSError):
        store.save_snapshot(
            LAST_RUN_KEY.format(topic_id),
            {"at": clock.now().isoformat(timespec="seconds"), "status": status},
        )


def _run_and_record(topic: Topic, deps: Deps, *, dry_run: bool = False) -> RunResult:
    try:
        result = run_topic(topic, deps, dry_run=dry_run)
    except Exception:
        if not dry_run:
            record_run(deps.store, topic.id, "failed", deps.clock)
        raise
    if not dry_run:
        record_run(deps.store, topic.id, result.status, deps.clock)
    return result


def _build_deps(
    settings: Settings, topics: Sequence[Topic], clock: Clock, allowed_hosts: Collection[str]
) -> Deps:
    http = new_http(allowed_hosts)
    store = Store(settings.data_dir)
    return Deps(
        http=http,
        clock=clock,
        store=store,
        summarizer=Summarizer(settings, http, clock),
        deliverer=make_deliverer(settings, http, clock, store=store, topics=topics),
        topics=list(topics),
        export_path=settings.site_data,
    )


def cmd_list(topics: Sequence[Topic], clock: Clock) -> int:
    now = clock.now()
    for t in topics:
        nxt = next_run(t.schedule, now)
        n_sources = len(t.sources) or len(next(iter((t.rotation or {}).values())).sources)
        kind = "ai" if t.ai else "plain"
        print(
            f"{t.emoji} {t.id:<16} {t.title:<34} {schedule_label(t.schedule):<22} "
            f"{kind:<5} {n_sources} src  next {nxt:%a %d %b %H:%M}"
        )
    return 0


def cmd_run(
    target: str,
    topics: Sequence[Topic],
    settings: Settings,
    clock: Clock,
    dry_run: bool,
    allowed_hosts: Collection[str],
) -> int:
    if target == "all":
        selected = list(topics)
    else:
        selected = [t for t in topics if t.id == target]
        if not selected:
            _err(f"unknown topic {target!r}; choose one of: {', '.join(t.id for t in topics)}")
            return 2
    deps = _build_deps(settings, topics, clock, allowed_hosts)
    results: list[RunResult] = []
    try:
        for topic in selected:
            try:
                results.append(_run_and_record(topic, deps, dry_run=dry_run))
            except Exception as exc:
                _err(f"[mybuddy] {topic.id}: crashed: {type(exc).__name__}: {exc}")
                results.append(RunResult(topic.id, "failed", errors=[str(exc)]))
    finally:
        deps.store.close()
    for r in results:
        print(f"{r.topic_id}: {r.status} (collected {r.collected}, new {r.new})", file=sys.stderr)
    return 0 if all(r.ok for r in results) else 1


def cmd_serve(
    topics: Sequence[Topic], settings: Settings, clock: Clock, allowed_hosts: Collection[str]
) -> int:
    deps = _build_deps(settings, topics, clock, allowed_hosts)
    scheduler = Scheduler(topics, lambda t: _run_and_record(t, deps), clock)
    scheduler.install_signal_handlers()
    print(f"[mybuddy] serving {len(topics)} topics (fake={settings.fake})", file=sys.stderr)
    try:
        scheduler.loop()
    finally:
        deps.store.close()
    return 0


def cmd_export(topics: Sequence[Topic], settings: Settings, clock: Clock) -> int:
    with Store(settings.data_dir) as store:
        export(topics, store, settings.site_data, clock.now())
    print(f"wrote {settings.site_data}")
    return 0
