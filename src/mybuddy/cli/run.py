"""mybuddy list | run | serve | export."""

from __future__ import annotations

import sys
from collections.abc import Collection, Sequence
from pathlib import Path

from mybuddy.allowlist import build_allowed_hosts
from mybuddy.cli._common import _err, new_http
from mybuddy.config import Config, Settings, Topic, load_config
from mybuddy.configedit import RELOAD_MARKER, make_reloader
from mybuddy.deliver import make_deliverer
from mybuddy.engine import Deps, RunResult, run_topic
from mybuddy.export import export, schedule_label
from mybuddy.models import Clock
from mybuddy.schedule import Scheduler, next_run
from mybuddy.store import Store
from mybuddy.summarize import Summarizer


def _build_deps(
    settings: Settings, topics: Sequence[Topic], clock: Clock, allowed_hosts: Collection[str]
) -> Deps:
    http = new_http(allowed_hosts)
    store = Store(settings.data_dir)
    return Deps(
        http=http,
        clock=clock,
        store=store,
        summarizer=Summarizer(settings, http, clock, store=store),
        deliverer=make_deliverer(settings, http, clock, store=store, topics=topics),
        topics=list(topics),
        export_path=settings.site_data,
        settings=settings,
    )


def cmd_list(topics: Sequence[Topic], clock: Clock) -> int:
    now = clock.now()
    for t in topics:
        nxt = next_run(t.schedule, now)
        rotation = list((t.rotation or {}).values())
        n_sources = len(t.sources) or (len(rotation[0].sources) if rotation else 0)
        kind = t.kind if t.kind != "news" else "ai" if t.ai else "plain"
        print(
            f"{t.emoji} {t.id:<16} {t.title:<34} {schedule_label(t.schedule):<22} "
            f"{kind:<6} {n_sources} src  next {nxt:%a %d %b %H:%M}"
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
                results.append(run_topic(topic, deps, dry_run=dry_run))
            except Exception as exc:
                _err(f"[mybuddy] {topic.id}: crashed: {type(exc).__name__}: {exc}")
                results.append(RunResult(topic.id, "failed", errors=[str(exc)]))
    finally:
        deps.store.close()
    for r in results:
        print(f"{r.topic_id}: {r.status} (collected {r.collected}, new {r.new})", file=sys.stderr)
    return 0 if all(r.ok for r in results) else 1


def cmd_serve(
    topics: Sequence[Topic],
    settings: Settings,
    clock: Clock,
    allowed_hosts: Collection[str],
    config_path: Path | None = None,
) -> int:
    state = {"deps": _build_deps(settings, topics, clock, allowed_hosts)}

    def on_reload(config: Config) -> None:
        # New sources may need new hosts: rebuild the HTTP client, deliverer and store.
        allowed = build_allowed_hosts(config.topics, settings, config.extra_allowed_hosts)
        old = state["deps"]
        state["deps"] = _build_deps(settings, config.topics, clock, allowed)
        old.store.close()

    reload = None
    if config_path is not None:
        marker = settings.data_dir / RELOAD_MARKER
        reload = make_reloader(config_path, marker, load_config, on_reload)
    scheduler = Scheduler(topics, lambda t: run_topic(t, state["deps"]), clock, reload=reload)
    scheduler.install_signal_handlers()
    print(f"[mybuddy] serving {len(topics)} topics (fake={settings.fake})", file=sys.stderr)
    try:
        scheduler.loop()
    finally:
        state["deps"].store.close()
    return 0


def cmd_export(topics: Sequence[Topic], settings: Settings, clock: Clock) -> int:
    with Store(settings.data_dir) as store:
        export(topics, store, settings.site_data, clock.now())
    print(f"wrote {settings.site_data}")
    return 0
