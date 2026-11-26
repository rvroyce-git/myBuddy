"""Shared data types. Every module talks to the others only through these."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Protocol


@dataclass(frozen=True)
class Item:
    """One news item collected from a source. Every item must have a real link."""

    title: str
    url: str
    source: str  # human-readable source name, e.g. "TechCrunch Startups"
    published: datetime | None = None  # timezone-aware UTC when known
    snippet: str = ""  # short plain-text description (no HTML), max ~300 chars
    extra: dict[str, Any] = field(default_factory=dict)  # e.g. {"stars_gained": 120}


@dataclass(frozen=True)
class ReportItem:
    """An item as shown in a report, with the AI's one-line explanation."""

    title: str
    url: str
    source: str
    blurb: str  # one plain sentence written by the AI (or the snippet in fallback mode)
    published: datetime | None = None


@dataclass(frozen=True)
class Report:
    """The finished report for one topic run."""

    topic_id: str
    title: str
    created_at: datetime  # timezone-aware
    headline: str  # one sentence: the single most important takeaway
    summary: str  # 2-4 short plain-text sentences
    items: tuple[ReportItem, ...]


class HttpClient(Protocol):
    """All network access goes through this so tests can use a fake."""

    def get_bytes(self, url: str, *, accept: str | None = None) -> bytes: ...

    def get_json(self, url: str) -> Any: ...

    def post_json(self, url: str, payload: Any, headers: dict[str, str] | None = None) -> Any: ...


class Clock(Protocol):
    def now(self) -> datetime: ...  # timezone-aware


class Source(Protocol):
    """A news source. collect() returns items newest first. It may raise on
    network or parse errors; the engine isolates each source's failure."""

    name: str

    def collect(self) -> list[Item]: ...
