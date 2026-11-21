"""Shared offline fakes for the engine tests."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from mybuddy.config import Schedule, Settings, Topic
from mybuddy.models import Item
from mybuddy.schedule import IST

Response = Any  # a value, an Exception instance to raise, or a callable(url, payload)


class FakeHttp:
    """Records every request; replies from queues (POST) or a URL map (GET)."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str, Any, dict[str, str] | None]] = []
        self.post_responses: list[Response] = []
        self.get_responses: dict[str, Response] = {}

    def _reply(self, r: Response, url: str, payload: Any = None) -> Any:
        if isinstance(r, BaseException):
            raise r
        if callable(r):
            return r(url, payload)
        return r

    def get_bytes(self, url: str, *, accept: str | None = None) -> bytes:
        self.calls.append(("GET", url, None, None))
        return bytes(self._reply(self.get_responses[url], url))

    def get_json(self, url: str, headers: dict[str, str] | None = None) -> Any:
        self.calls.append(("GET", url, None, headers))
        if url not in self.get_responses:
            raise AssertionError(f"unexpected GET {url}")
        return self._reply(self.get_responses[url], url)

    def post_json(self, url: str, payload: Any, headers: dict[str, str] | None = None) -> Any:
        self.calls.append(("POST", url, payload, headers))
        r = self.post_responses.pop(0) if self.post_responses else None
        return self._reply(r, url, payload)

    @property
    def posts(self) -> list[tuple[str, str, Any, dict[str, str] | None]]:
        return [c for c in self.calls if c[0] == "POST"]


class FixedClock:
    def __init__(self, when: datetime) -> None:
        self.when = when

    def now(self) -> datetime:
        return self.when

    def advance(self, **kw: float) -> None:
        self.when += timedelta(**kw)


@dataclass
class FakeSource:
    name: str
    items: list[Item] = field(default_factory=list)
    error: Exception | None = None
    on_collect: Callable[[], None] | None = None

    def collect(self) -> list[Item]:
        if self.on_collect:
            self.on_collect()
        if self.error:
            raise self.error
        return list(self.items)


def make_item(n: int, source: str = "Feed", **kw: Any) -> Item:
    defaults: dict[str, Any] = {
        "title": f"Story {n}",
        "url": f"https://example.com/{source.lower().replace(' ', '-')}/{n}",
        "source": source,
        "published": datetime(2026, 11, 23, 1, 2, 3, tzinfo=UTC),
        "snippet": f"Snippet for story {n}.",
    }
    defaults.update(kw)
    return Item(**defaults)


def make_topic(**kw: Any) -> Topic:
    defaults: dict[str, Any] = {
        "id": "ai_news",
        "title": "AI news",
        "emoji": "🧠",
        "description": "AI things.",
        "schedule": Schedule(time="06:30"),
        "ai": True,
        "prompt": "ai_news.md",
        "sources": ({"type": "fake", "name": "Feed"},),
    }
    defaults.update(kw)
    return Topic(**defaults)


# Monday 23 Nov 2026, 06:30 IST
MONDAY = datetime(2026, 11, 23, 6, 30, tzinfo=IST)


@pytest.fixture
def clock() -> FixedClock:
    return FixedClock(MONDAY)


@pytest.fixture
def http() -> FakeHttp:
    return FakeHttp()


@pytest.fixture
def data_dir(tmp_path: Path) -> Path:
    return tmp_path / "data"


@pytest.fixture
def settings(data_dir: Path, tmp_path: Path) -> Settings:
    return Settings(
        groq_api_key="gsk_test",
        gemini_api_key=None,
        discord_webhook_url=None,
        data_dir=data_dir,
        site_data=tmp_path / "site" / "reports.json",
        fake=False,
    )
