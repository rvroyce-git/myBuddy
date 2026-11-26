"""Product recalls from free US government APIs (no keys):

* openFDA enforcement reports (drug, device or food recalls), newest report first.
* US CPSC recalls (consumer products) from saferproducts.gov.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from typing import Any
from urllib.parse import quote, urlencode

from mybuddy.http import HttpError
from mybuddy.models import Clock, HttpClient, Item
from mybuddy.sources._common import (
    clean_snippet,
    clean_title,
    clip,
    collapse,
    is_web_url,
    newest_first,
    parse_iso,
    spec_int,
    spec_str,
)

OPENFDA_API = "https://api.fda.gov"
OPENFDA_CATEGORIES = ("drug", "device", "food")
# The FDA's public enforcement report page for one recall number (displayed, never fetched).
FDA_RECALL_PAGE = (
    "https://www.accessdata.fda.gov/scripts/ires/index.cfm"
    "?event=ires.dspBriefRecallNumber&RecallNumber="
)
CPSC_API = "https://www.saferproducts.gov/RestWebServices/Recall"


def _text(entry: dict[str, Any], key: str) -> str:
    value = entry.get(key)
    return collapse(value) if isinstance(value, str) else ""


def _fda_date(value: str) -> datetime | None:
    try:
        day = datetime.strptime(value, "%Y%m%d").date()
    except ValueError:
        return None
    return datetime(day.year, day.month, day.day, tzinfo=UTC)


def openfda_url(category: str, start: date, end: date, limit: int) -> str:
    params = {
        "search": f"report_date:[{start:%Y%m%d} TO {end:%Y%m%d}]",
        "sort": "report_date:desc",
        "limit": str(limit),
    }
    return f"{OPENFDA_API}/{category}/enforcement.json?" + urlencode(params)


class OpenFdaRecallsSource:
    def __init__(
        self,
        name: str,
        category: str,
        http: HttpClient,
        clock: Clock,
        *,
        days: int = 14,
        limit: int = 5,
    ) -> None:
        if category not in OPENFDA_CATEGORIES:
            raise ValueError(
                f"source {name!r}: 'category' must be one of {', '.join(OPENFDA_CATEGORIES)}"
            )
        self.name = name
        self.category = category
        self.days = days
        self.limit = limit
        self._http = http
        self._clock = clock

    def url(self) -> str:
        today = self._clock.now().astimezone(UTC).date()
        return openfda_url(self.category, today - timedelta(days=self.days), today, self.limit)

    def collect(self) -> list[Item]:
        try:
            payload = self._http.get_json(self.url())
        except HttpError as exc:
            if exc.status == 404:  # openFDA answers "no matches" with 404 NOT_FOUND
                return []
            raise
        results = payload.get("results") if isinstance(payload, dict) else None
        if not isinstance(results, list):
            raise ValueError(f"{self.name}: unexpected openFDA response")
        items: list[Item] = []
        seen: set[str] = set()
        for entry in results:
            if not isinstance(entry, dict):
                continue
            number = _text(entry, "recall_number")
            if not number or number in seen:
                continue
            seen.add(number)
            firm = _text(entry, "recalling_firm")
            product = clip(_text(entry, "product_description"), 140)
            title = f"{firm}: {product}" if firm and product else firm or product
            if not title:
                continue
            parts = [
                _text(entry, "classification"),
                _text(entry, "reason_for_recall"),
            ]
            where = _text(entry, "distribution_pattern")
            if where:
                parts.append(f"Distribution: {clip(where, 80)}")
            items.append(
                Item(
                    title=clean_title(title),
                    url=FDA_RECALL_PAGE + quote(number, safe=""),
                    source=self.name,
                    published=_fda_date(_text(entry, "report_date")),
                    snippet=clean_snippet(" · ".join(p for p in parts if p)),
                    extra={"classification": _text(entry, "classification") or None},
                )
            )
        return newest_first(items)[: self.limit]


def _first_name(entry: dict[str, Any], key: str) -> str:
    values = entry.get(key)
    if isinstance(values, list):
        for value in values:
            if isinstance(value, dict) and isinstance(value.get("Name"), str):
                text = collapse(value["Name"])
                if text:
                    return text
    return ""


def cpsc_url(start: date) -> str:
    return CPSC_API + "?" + urlencode({"format": "json", "RecallDateStart": start.isoformat()})


class CpscRecallsSource:
    def __init__(
        self, name: str, http: HttpClient, clock: Clock, *, days: int = 7, limit: int = 8
    ) -> None:
        self.name = name
        self.days = days
        self.limit = limit
        self._http = http
        self._clock = clock

    def url(self) -> str:
        today = self._clock.now().astimezone(UTC).date()
        return cpsc_url(today - timedelta(days=self.days))

    def collect(self) -> list[Item]:
        payload = self._http.get_json(self.url())
        if not isinstance(payload, list):
            raise ValueError(f"{self.name}: unexpected CPSC response")
        items: list[Item] = []
        for entry in payload:
            if not isinstance(entry, dict):
                continue
            title = clean_title(_text(entry, "Title"))
            url = _text(entry, "URL")
            if url.startswith("http://"):
                url = "https://" + url[len("http://") :]
            if not title or not is_web_url(url):
                continue
            products = entry.get("Products")
            units = ""
            if isinstance(products, list) and products and isinstance(products[0], dict):
                units = _text(products[0], "NumberOfUnits")
            parts = [_first_name(entry, "Hazards"), _first_name(entry, "Remedies")]
            if units:
                parts.append(f"Units: {clip(units, 40)}")
            items.append(
                Item(
                    title=title,
                    url=url,
                    source=self.name,
                    published=parse_iso(_text(entry, "RecallDate")),
                    snippet=clean_snippet(" · ".join(clip(p, 140) for p in parts if p)),
                )
            )
        return newest_first(items)[: self.limit]


def build_openfda_recalls(
    spec: dict[str, Any], http: HttpClient, clock: Clock
) -> OpenFdaRecallsSource:
    return OpenFdaRecallsSource(
        spec_str(spec, "name"),
        spec_str(spec, "category"),
        http,
        clock,
        days=spec_int(spec, "days", 14),
        limit=spec_int(spec, "limit", 5),
    )


def build_cpsc_recalls(spec: dict[str, Any], http: HttpClient, clock: Clock) -> CpscRecallsSource:
    return CpscRecallsSource(
        spec_str(spec, "name"),
        http,
        clock,
        days=spec_int(spec, "days", 7),
        limit=spec_int(spec, "limit", 8),
    )
