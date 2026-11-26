from __future__ import annotations

from datetime import UTC, datetime

from test_sources_support import FakeHttp, FixedClock, fixture_json

from mybuddy.sources import build_source
from mybuddy.sources.cisa_kev import KEV_URL


def collect(clock: FixedClock | None = None, **spec: object) -> list:  # type: ignore[type-arg]
    full = {"type": "cisa_kev", "name": "CISA KEV", **spec}
    return build_source(
        full, FakeHttp({KEV_URL: fixture_json("cisa_kev.json")}), clock or FixedClock()
    ).collect()


def test_default_window_two_days() -> None:
    items = collect()  # clock 2026-11-22: window is Nov 20..22
    assert len(items) == 4
    assert all(it.published == datetime(2026, 11, 20, tzinfo=UTC) for it in items)
    ids = [it.title.split(":")[0] for it in items]
    assert ids == sorted(ids, reverse=True)
    first = items[0]
    assert first.url == f"https://nvd.nist.gov/vuln/detail/{ids[0]}"
    assert " — " in first.title
    assert "Required action:" in first.snippet and "Due " in first.snippet
    assert len(first.snippet) <= 300
    assert first.extra["due_date"]


def test_title_format() -> None:
    entry = fixture_json("cisa_kev.json")["vulnerabilities"][2]
    items = {it.url: it for it in collect()}
    it = items["https://nvd.nist.gov/vuln/detail/" + entry["cveID"]]
    expected = (
        f"{entry['cveID']}: {entry['vendorProject']} {entry['product']} — "
        f"{entry['vulnerabilityName']}"
    )
    assert it.title == expected[:200] or it.title.endswith("…")


def test_wider_and_narrower_windows() -> None:
    assert len(collect(days=4)) == 6
    items = collect(days=4)
    dates = [it.published for it in items]
    assert dates == sorted(dates, reverse=True)
    assert collect(days=1) == []  # Nov 21..22 has nothing
    on_the_day = FixedClock(datetime(2026, 11, 20, 23, 0, tzinfo=UTC))
    assert len(collect(on_the_day, days=0)) == 4
    assert len(collect(days=10000)) == 8
    assert len(collect(days=4, limit=3)) == 3


def test_future_and_bad_entries_skipped() -> None:
    data = {
        "vulnerabilities": [
            {"cveID": "CVE-2026-0001", "dateAdded": "2026-11-23"},  # future
            {"cveID": "not-a-cve", "dateAdded": "2026-11-21"},
            {"cveID": "CVE-2026-0002", "dateAdded": "bad"},
            {"cveID": "CVE-2026-0003", "dateAdded": "2026-11-21", "vendorProject": "Acme"},
            "junk",
        ]
    }
    spec = {"type": "cisa_kev", "name": "K"}
    items = build_source(spec, FakeHttp({KEV_URL: data}), FixedClock()).collect()
    assert [it.title for it in items] == ["CVE-2026-0003: Acme"]
    assert items[0].snippet == ""
