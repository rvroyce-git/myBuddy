"""US Federal Register documents search (free JSON API, no key): recent rules and
presidential documents matching a search term, most relevant first."""

from __future__ import annotations

from datetime import UTC, timedelta
from typing import Any
from urllib.parse import urlencode

from mybuddy.models import Clock, HttpClient, Item
from mybuddy.sources._common import (
    clean_snippet,
    clean_title,
    collapse,
    parse_iso,
    spec_int,
    spec_str,
    spec_str_list,
)

FR_API = "https://www.federalregister.gov/api/v1/documents.json"
# Rules, proposed rules and presidential documents; notices are mostly routine filings.
DEFAULT_TYPES = ("RULE", "PRORULE", "PRESDOCU")
_FIELDS = (
    "title",
    "abstract",
    "html_url",
    "publication_date",
    "type",
    "agencies",
    "document_number",
)


def federal_register_url(term: str, since: str, types: tuple[str, ...], limit: int) -> str:
    params: list[tuple[str, str]] = [
        ("conditions[term]", term),
        ("conditions[publication_date][gte]", since),
    ]
    params += [("conditions[type][]", t) for t in types]
    params += [("order", "relevance"), ("per_page", str(limit))]
    params += [("fields[]", f) for f in _FIELDS]
    return FR_API + "?" + urlencode(params)


def _agencies(doc: dict[str, Any]) -> str:
    names: list[str] = []
    for agency in doc.get("agencies") or []:
        if isinstance(agency, dict):
            name = agency.get("name") or agency.get("raw_name")
            if isinstance(name, str) and name.strip() and name.strip() not in names:
                names.append(collapse(name))
    return ", ".join(names[:2])


class FederalRegisterSource:
    def __init__(
        self,
        name: str,
        term: str,
        http: HttpClient,
        clock: Clock,
        *,
        days: int = 7,
        limit: int = 5,
        types: tuple[str, ...] = DEFAULT_TYPES,
    ) -> None:
        self.name = name
        self.term = term
        self.days = days
        self.limit = limit
        self.types = types
        self._http = http
        self._clock = clock

    def url(self) -> str:
        since = (self._clock.now().astimezone(UTC) - timedelta(days=self.days)).date()
        return federal_register_url(self.term, since.isoformat(), self.types, self.limit)

    def collect(self) -> list[Item]:
        payload = self._http.get_json(self.url())
        if not isinstance(payload, dict):
            raise ValueError(f"{self.name}: unexpected Federal Register response")
        results = payload.get("results", [])  # no matches: the key is absent
        if not isinstance(results, list):
            raise ValueError(f"{self.name}: unexpected Federal Register response")
        items: list[Item] = []
        for doc in results:
            if not isinstance(doc, dict):
                continue
            title = clean_title(str(doc.get("title") or ""))
            url = doc.get("html_url")
            if not title or not isinstance(url, str) or not url.startswith("https://"):
                continue
            kind = collapse(str(doc.get("type") or ""))
            who = _agencies(doc)
            prefix = " · ".join(p for p in (kind, who) if p)
            abstract = str(doc.get("abstract") or "")
            snippet = f"{prefix}: {abstract}" if prefix and abstract else prefix or abstract
            items.append(
                Item(
                    title=title,
                    url=url,
                    source=self.name,
                    published=parse_iso(doc.get("publication_date")),
                    snippet=clean_snippet(snippet),
                    extra={"document_type": kind or None},
                )
            )
        return items[: self.limit]  # relevance order, as the API returned it


def _types(spec: dict[str, Any]) -> tuple[str, ...]:
    if "types" not in spec:
        return DEFAULT_TYPES
    values = spec_str_list(spec, "types")
    bad = [v for v in values if not v.isalpha() or not v.isupper()]
    if bad:
        raise ValueError(f"source {spec.get('name')!r}: 'types' must be codes like RULE")
    return tuple(values)


def build_federal_register(
    spec: dict[str, Any], http: HttpClient, clock: Clock
) -> FederalRegisterSource:
    return FederalRegisterSource(
        spec_str(spec, "name"),
        spec_str(spec, "term"),
        http,
        clock,
        days=spec_int(spec, "days", 7),
        limit=spec_int(spec, "limit", 5),
        types=_types(spec),
    )
