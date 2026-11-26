from __future__ import annotations

import io
import json
from collections.abc import Iterator
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest
from conftest import MONDAY, FakeHttp, FakeSource, FixedClock, make_item, make_topic

from mybuddy.config import RotationEntry, Schedule, Settings, Topic
from mybuddy.engine import Deps, run_topic, snapshot_key
from mybuddy.models import Clock, HttpClient, Report, Source
from mybuddy.schedule import IST
from mybuddy.store import Store
from mybuddy.summarize import Summarizer


class RecordingDeliverer:
    def __init__(self, fail: bool = False) -> None:
        self.fail = fail
        self.delivered: list[Report] = []

    def deliver(self, report: Report, emoji: str) -> None:
        if self.fail:
            raise RuntimeError("discord down")
        self.delivered.append(report)


_OPEN_STORES: list[Store] = []


@pytest.fixture(autouse=True)
def _close_stores() -> Iterator[None]:
    yield
    while _OPEN_STORES:
        _OPEN_STORES.pop().close()


def make_deps(
    settings: Settings,
    clock: FixedClock,
    sources: dict[str, FakeSource],
    *,
    deliverer: RecordingDeliverer | None = None,
    topics: list[Topic] | None = None,
) -> tuple[Deps, list[dict[str, Any]]]:
    built: list[dict[str, Any]] = []

    def build(spec: dict[str, Any], http: HttpClient, c: Clock) -> Source:
        built.append(spec)
        if spec["name"] not in sources:
            raise ValueError(f"no such source {spec['name']}")
        return sources[spec["name"]]

    http = FakeHttp()
    store = Store(settings.data_dir)
    _OPEN_STORES.append(store)
    deps = Deps(
        http=http,
        clock=clock,
        store=store,
        summarizer=Summarizer(replace(settings, fake=True), http, clock),
        deliverer=deliverer or RecordingDeliverer(),
        topics=topics or [],
        export_path=settings.site_data,
        build_source=build,
        log=io.StringIO(),
        out=io.StringIO(),
    )
    return deps, built


TWO_SOURCES = (
    {"type": "fake", "name": "A"},
    {"type": "fake", "name": "B"},
)


def test_failing_source_is_isolated(settings: Settings, clock: FixedClock) -> None:
    topic = make_topic(sources=TWO_SOURCES)
    deps, _ = make_deps(
        settings,
        clock,
        {
            "A": FakeSource("A", error=RuntimeError("feed broke")),
            "B": FakeSource("B", [make_item(1, "B")]),
        },
    )
    result = run_topic(topic, deps)
    assert result.status == "delivered"
    assert "source 'A' failed: RuntimeError: feed broke" in deps.log.getvalue()  # type: ignore[union-attr]
    assert [i.source for i in result.report.items] == ["B"]  # type: ignore[union-attr]


def test_interleaves_dedupes_and_drops_non_https(settings: Settings, clock: FixedClock) -> None:
    topic = make_topic(sources=TWO_SOURCES, max_items=3)
    a = [make_item(i, "A") for i in range(3)] + [make_item(9, "A", url="http://insecure/x")]
    b = [make_item(0, "A"), make_item(5, "B")]  # first is a duplicate URL of A's item 0
    deps, _ = make_deps(settings, clock, {"A": FakeSource("A", a), "B": FakeSource("B", b)})
    result = run_topic(topic, deps)
    assert result.collected == 4
    urls = [i.url for i in result.report.items]  # type: ignore[union-attr]
    assert urls == [a[0].url, a[1].url, b[1].url]  # round-robin; b[0] was a duplicate
    # all fresh items are marked seen, even those beyond max_items
    assert deps.store.is_seen(topic.id, a[2].url)


def test_seen_marked_only_after_delivery(settings: Settings, clock: FixedClock) -> None:
    topic = make_topic()
    item = make_item(1)
    failing = RecordingDeliverer(fail=True)
    deps, _ = make_deps(settings, clock, {"Feed": FakeSource("Feed", [item])}, deliverer=failing)
    result = run_topic(topic, deps)
    assert result.status == "failed" and not result.ok
    assert not deps.store.is_seen(topic.id, item.url)
    assert deps.store.latest_report(topic.id) is None

    deps.deliverer = RecordingDeliverer()
    assert run_topic(topic, deps).status == "delivered"
    assert deps.store.is_seen(topic.id, item.url)
    assert deps.store.latest_report(topic.id) is not None


def test_no_new_items_delivers_nothing(settings: Settings, clock: FixedClock) -> None:
    topic = make_topic()
    item = make_item(1)
    deliverer = RecordingDeliverer()
    deps, _ = make_deps(settings, clock, {"Feed": FakeSource("Feed", [item])}, deliverer=deliverer)
    deps.store.mark_seen(topic.id, [item.url], MONDAY)
    result = run_topic(topic, deps)
    assert result.status == "no_new_items" and deliverer.delivered == []
    assert "no new items" in deps.log.getvalue()  # type: ignore[union-attr]


def test_all_sources_failing_is_failure(settings: Settings, clock: FixedClock) -> None:
    deps, _ = make_deps(settings, clock, {"Feed": FakeSource("Feed", error=OSError("down"))})
    assert run_topic(make_topic(), deps).status == "failed"


def test_dry_run_changes_nothing(settings: Settings, clock: FixedClock) -> None:
    topic = make_topic(
        sources=({"type": "github_trending", "name": "GH"},),
    )

    holder: dict[str, Any] = {}

    def mutate() -> None:
        holder["spec"]["_state"]["org/repo"] = 99

    src = FakeSource("GH", [make_item(1, "GH")], on_collect=mutate)
    deliverer = RecordingDeliverer()
    deps, _ = make_deps(settings, clock, {"GH": src}, deliverer=deliverer, topics=[topic])
    orig_build = deps.build_source

    def build(spec: dict[str, Any], http: HttpClient, c: Clock) -> Source:
        holder["spec"] = spec
        return orig_build(spec, http, c)

    deps.build_source = build
    result = run_topic(topic, deps, dry_run=True)
    assert result.status == "dry_run"
    assert "(fake)" in deps.out.getvalue()  # type: ignore[union-attr]
    assert deliverer.delivered == []
    assert not deps.store.is_seen(topic.id, make_item(1, "GH").url)
    assert deps.store.load_snapshot(snapshot_key(topic.id, "GH")) == {}
    assert deps.store.latest_report(topic.id) is None
    assert not settings.site_data.exists()


def test_github_state_loaded_and_saved(settings: Settings, clock: FixedClock) -> None:
    topic = make_topic(id="github_trending", sources=({"type": "github_trending", "name": "GH"},))
    src = FakeSource("GH", [make_item(1, "GH")])
    deps, built = make_deps(settings, clock, {"GH": src}, topics=[topic])
    key = snapshot_key(topic.id, "GH")
    deps.store.save_snapshot(key, {"old/repo": 5})
    src.on_collect = lambda: built[-1]["_state"].update({"new/repo": 7})
    assert run_topic(topic, deps).status == "delivered"
    assert "_state" not in topic.sources[0]  # config specs are never mutated
    assert deps.store.load_snapshot(key) == {"old/repo": 5, "new/repo": 7}


def test_kev_plain_path_no_ai(settings: Settings, clock: FixedClock) -> None:
    topic = make_topic(
        id="kev_alerts",
        title="Actively exploited vulnerabilities",
        ai=False,
        prompt=None,
        schedule=Schedule(every_minutes=60),
    )
    deliverer = RecordingDeliverer()
    deps, _ = make_deps(
        settings,
        clock,
        {"Feed": FakeSource("Feed", [make_item(1, title="CVE-2026-1")])},
        deliverer=deliverer,
        topics=[topic],
    )
    deps.summarizer = None  # type: ignore[assignment]  # must not be touched
    result = run_topic(topic, deps)
    assert result.status == "delivered"
    [report] = deliverer.delivered
    assert report.headline == "1 new item: Actively exploited vulnerabilities"
    assert report.items[0].blurb == "Snippet for story 1."
    exported = json.loads(settings.site_data.read_text(encoding="utf-8"))
    assert exported["topics"] == []  # hourly KEV alerts are Discord-only, not site widgets


def test_kev_alerts_end_to_end_plain_path(settings: Settings) -> None:
    """Real KEV source + fixture JSON -> engine -> formatted message; seen only after delivery."""
    from test_sources_support import fixture_json

    from mybuddy.deliver.format import format_telegram
    from mybuddy.sources import build_source
    from mybuddy.sources.cisa_kev import KEV_URL

    vulns = fixture_json("cisa_kev.json")["vulnerabilities"]
    fresh = [v for v in vulns if v["dateAdded"] == "2026-11-20"]
    assert fresh
    topic = make_topic(
        id="kev_alerts",
        title="Actively exploited vulnerabilities",
        emoji="🚨",
        ai=False,
        prompt=None,
        schedule=Schedule(every_minutes=60),
        sources=({"type": "cisa_kev", "name": "CISA KEV", "days": 1},),
    )
    clock = FixedClock(datetime(2026, 11, 21, 6, 0, tzinfo=IST))
    failing = RecordingDeliverer(fail=True)
    deps, _ = make_deps(settings, clock, {}, deliverer=failing, topics=[topic])
    deps.http.get_responses[KEV_URL] = fixture_json("cisa_kev.json")  # type: ignore[attr-defined]
    deps.build_source = build_source
    deps.summarizer = None  # type: ignore[assignment]  # the plain path never uses AI

    urls = [f"https://nvd.nist.gov/vuln/detail/{v['cveID']}" for v in fresh]
    assert run_topic(topic, deps).status == "failed"
    assert not any(deps.store.is_seen(topic.id, u) for u in urls)

    deliverer = RecordingDeliverer()
    deps.deliverer = deliverer
    assert run_topic(topic, deps).status == "delivered"
    [report] = deliverer.delivered
    message = format_telegram(report, topic.emoji)
    for v in fresh:
        assert v["cveID"] in message
        assert f"{v['vendorProject']} {v['product']}" in message
        assert f'href="https://nvd.nist.gov/vuln/detail/{v["cveID"]}"' in message
    assert all(deps.store.is_seen(topic.id, u) for u in urls)

    deps.deliverer = second = RecordingDeliverer()
    assert run_topic(topic, deps).status == "no_new_items"
    assert second.delivered == []


ROTATION = {
    "mon": RotationEntry("RAG", ({"type": "fake", "name": "Mon"},)),
    "tue": RotationEntry("Agents", ({"type": "fake", "name": "Tue"},)),
}


@pytest.mark.parametrize(
    ("when", "expected_source", "status"),
    [
        (datetime(2026, 11, 23, 7, 30, tzinfo=IST), "Mon", "delivered"),
        (datetime(2026, 11, 24, 7, 30, tzinfo=IST), "Tue", "delivered"),
        (datetime(2026, 11, 28, 7, 30, tzinfo=IST), None, "skipped"),  # Saturday
    ],
)
def test_rotation(
    settings: Settings, when: datetime, expected_source: str | None, status: str
) -> None:
    topic = make_topic(id="deep_dive", title="Deep dive", sources=(), rotation=ROTATION)
    clock = FixedClock(when)
    sources = {n: FakeSource(n, [make_item(1, n)]) for n in ("Mon", "Tue")}
    deliverer = RecordingDeliverer()
    deps, built = make_deps(settings, clock, sources, deliverer=deliverer)
    result = run_topic(topic, deps)
    assert result.status == status
    if expected_source:
        assert [s["name"] for s in built] == [expected_source]
        assert deliverer.delivered[0].title.startswith("Deep dive: ")
    else:
        assert built == [] and deliverer.delivered == []


def test_export_failure_does_not_fail_run(
    settings: Settings, clock: FixedClock, tmp_path: Path
) -> None:
    blocker = tmp_path / "file"
    blocker.write_text("x", encoding="utf-8")
    s = replace(settings, site_data=blocker / "reports.json")
    topic = make_topic()
    deps, _ = make_deps(s, clock, {"Feed": FakeSource("Feed", [make_item(1)])}, topics=[topic])
    assert run_topic(topic, deps).status == "delivered"
    assert "export failed" in deps.log.getvalue()  # type: ignore[union-attr]
