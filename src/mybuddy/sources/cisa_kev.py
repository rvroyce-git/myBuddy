"""CISA Known Exploited Vulnerabilities catalog: entries added in the last N days."""

from __future__ import annotations

import re
from datetime import UTC, date, datetime, timedelta
from typing import Any

from mybuddy.models import Clock, HttpClient, Item
from mybuddy.sources._common import (
    SNIPPET_MAX,
    clean_title,
    clip,
    collapse,
    spec_int,
    spec_str,
)

KEV_URL = "https://www.cisa.gov/sites/default/files/feeds/known_exploited_vulnerabilities.json"
NVD_URL = "https://nvd.nist.gov/vuln/detail/"
_CVE = re.compile(r"^CVE-\d{4}-\d{4,}$")


def _text(entry: dict[str, Any], key: str) -> str:
    value = entry.get(key)
    return collapse(value) if isinstance(value, str) else ""


class CisaKevSource:
    def __init__(
        self, name: str, http: HttpClient, clock: Clock, *, days: int = 2, limit: int = 25
    ) -> None:
        self.name = name
        self.days = days
        self.limit = limit
        self._http = http
        self._clock = clock

    def collect(self) -> list[Item]:
        payload = self._http.get_json(KEV_URL)
        vulns = payload.get("vulnerabilities") if isinstance(payload, dict) else None
        if not isinstance(vulns, list):
            raise ValueError(f"{self.name}: unexpected KEV catalog response")
        today = self._clock.now().astimezone(UTC).date()
        earliest = today - timedelta(days=self.days)
        found: list[tuple[date, str, Item]] = []
        for entry in vulns:
            if not isinstance(entry, dict):
                continue
            cve = _text(entry, "cveID")
            try:
                added = date.fromisoformat(_text(entry, "dateAdded"))
            except ValueError:
                continue
            if not _CVE.match(cve) or not earliest <= added <= today:
                continue
            found.append((added, cve, self._to_item(entry, cve, added)))
        found.sort(key=lambda f: (f[0], f[1]), reverse=True)
        return [item for _, _, item in found[: self.limit]]

    def _to_item(self, entry: dict[str, Any], cve: str, added: date) -> Item:
        product = " ".join(p for p in (_text(entry, "vendorProject"), _text(entry, "product")) if p)
        name = _text(entry, "vulnerabilityName")
        title = f"{cve}: {product}" if product else cve
        if name:
            title = f"{title} — {name}"
        action = _text(entry, "requiredAction")
        due = _text(entry, "dueDate")
        action_part = f" Required action: {clip(action, 110)}" if action else ""
        due_part = f" Due {due}." if due else ""
        budget = SNIPPET_MAX - len(action_part) - len(due_part)
        snippet = (clip(_text(entry, "shortDescription"), budget) + action_part + due_part).strip()
        ransomware = _text(entry, "knownRansomwareCampaignUse")
        return Item(
            title=clean_title(title),
            url=NVD_URL + cve,
            source=self.name,
            published=datetime(added.year, added.month, added.day, tzinfo=UTC),
            snippet=snippet,
            extra={"due_date": due or None, "ransomware": ransomware or None},
        )


def build_cisa_kev(spec: dict[str, Any], http: HttpClient, clock: Clock) -> CisaKevSource:
    return CisaKevSource(
        spec_str(spec, "name"),
        http,
        clock,
        days=spec_int(spec, "days", 2, minimum=0),
        limit=spec_int(spec, "limit", 25),
    )
