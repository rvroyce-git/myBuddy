"""Federal Register, openFDA and CPSC sources against trimmed real responses (2026-11-26)."""

from __future__ import annotations

from datetime import UTC, datetime
from urllib.parse import parse_qs, urlsplit

import pytest
from test_sources_support import FakeHttp, FixedClock, fixture_bytes, fixture_json

from mybuddy.http import HttpError
from mybuddy.sources import build_source
from mybuddy.sources.federal_register import FR_API
from mybuddy.sources.recalls import CPSC_API, FDA_RECALL_PAGE, OPENFDA_API
from mybuddy.sources.rss import parse_feed

NOW = datetime(2026, 11, 26, 3, 0, tzinfo=UTC)


def _collect(spec: dict[str, object], routes: dict[str, object]) -> tuple[list, FakeHttp]:  # type: ignore[type-arg]
    http = FakeHttp(routes)
    return build_source({"name": "S", **spec}, http, FixedClock(NOW)).collect(), http


# ----------------------------------------------------------------- Federal Register


def test_federal_register_parses_fixture_and_builds_query() -> None:
    items, http = _collect(
        {"type": "federal_register", "term": '"artificial intelligence"', "limit": 3},
        {FR_API + "*": fixture_json("federal_register.json")},
    )
    assert len(items) == 3
    first = items[0]
    assert first.url.startswith("https://www.federalregister.gov/documents/")
    assert first.title and first.published is not None and first.published.tzinfo
    assert first.snippet.startswith(("Rule", "Proposed Rule", "Presidential"))
    assert len(first.snippet) <= 300
    query = parse_qs(urlsplit(http.calls[0]).query)
    assert query["conditions[term]"] == ['"artificial intelligence"']
    assert query["conditions[publication_date][gte]"] == ["2026-11-19"]
    assert query["conditions[type][]"] == ["RULE", "PRORULE", "PRESDOCU"]
    assert query["order"] == ["relevance"] and query["per_page"] == ["3"]


def test_federal_register_empty_and_errors() -> None:
    spec = {"type": "federal_register", "term": "zz"}
    no_match = {"description": "Documents matching 'zz'", "count": 0}  # real "no match" shape
    assert _collect(spec, {FR_API + "*": no_match})[0] == []
    junk = {"results": [{"title": "No link"}, {"html_url": "https://x.example/"}, "bad"]}
    assert _collect(spec, {FR_API + "*": junk})[0] == []
    with pytest.raises(ValueError, match="unexpected"):
        _collect(spec, {FR_API + "*": {"results": "nope"}})
    with pytest.raises(ValueError, match="types"):
        _collect({**spec, "types": ["rule"]}, {})


# ----------------------------------------------------------------------- openFDA


@pytest.mark.parametrize("category", ["drug", "device"])
def test_openfda_parses_fixture(category: str) -> None:
    items, http = _collect(
        {"type": "openfda_recalls", "category": category, "days": 14},
        {OPENFDA_API + "*": fixture_json(f"openfda_{category}.json")},
    )
    assert len(items) == 3
    assert all(i.url.startswith(FDA_RECALL_PAGE) for i in items)
    assert len({i.url for i in items}) == 3
    assert all(i.published == datetime(2026, 11, 18, tzinfo=UTC) for i in items)
    assert all(i.snippet.startswith("Class ") for i in items)
    url = http.calls[0]
    assert url.startswith(f"{OPENFDA_API}/{category}/enforcement.json?")
    assert parse_qs(urlsplit(url).query)["search"] == ["report_date:[20261112 TO 20261126]"]


def test_openfda_no_matches_is_empty_and_other_errors_raise() -> None:
    spec = {"type": "openfda_recalls", "category": "drug"}
    not_found = HttpError("HTTP 404", status=404, body='{"error":{"code":"NOT_FOUND"}}')
    assert _collect(spec, {OPENFDA_API + "*": not_found})[0] == []
    with pytest.raises(HttpError):
        _collect(spec, {OPENFDA_API + "*": HttpError("HTTP 500", status=500)})
    with pytest.raises(ValueError, match="unexpected"):
        _collect(spec, {OPENFDA_API + "*": {"meta": {}}})
    with pytest.raises(ValueError, match="category"):
        _collect({**spec, "category": "cosmetics"}, {})


# -------------------------------------------------------------------------- CPSC


def test_cpsc_parses_fixture() -> None:
    items, http = _collect(
        {"type": "cpsc_recalls", "days": 7}, {CPSC_API + "*": fixture_json("cpsc_recalls.json")}
    )
    assert len(items) == 4
    assert all(i.url.startswith("https://") and "cpsc.gov" in i.url for i in items)
    assert all(i.published == datetime(2026, 11, 19, tzinfo=UTC) for i in items)
    assert "Units:" in items[0].snippet or items[0].snippet
    query = parse_qs(urlsplit(http.calls[0]).query)
    assert query == {"format": ["json"], "RecallDateStart": ["2026-11-19"]}


def test_cpsc_empty_and_bad() -> None:
    spec = {"type": "cpsc_recalls"}
    assert _collect(spec, {CPSC_API + "*": []})[0] == []
    bad = [{"Title": "No url"}, {"URL": "https://www.cpsc.gov/x"}, 3]
    assert _collect(spec, {CPSC_API + "*": bad})[0] == []
    with pytest.raises(ValueError, match="unexpected"):
        _collect(spec, {CPSC_API + "*": {"error": "x"}})


# ------------------------------------------------------------- EU press corner (rss)


def test_eu_press_corner_feed_parses() -> None:
    items = parse_feed(fixture_bytes("eu_presscorner_ai.xml"), "European Commission")
    assert items and all(i.url.startswith("https://ec.europa.eu/") for i in items)
    assert all(i.published is not None for i in items)
