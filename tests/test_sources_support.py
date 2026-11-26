"""Offline fakes shared by the source tests (no tests in this module)."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

FIXTURES = Path(__file__).parent / "fixtures"
NOW = datetime(2026, 11, 22, 0, 0, tzinfo=UTC)


def fixture_bytes(name: str) -> bytes:
    return (FIXTURES / name).read_bytes()


def fixture_json(name: str) -> Any:
    return json.loads(fixture_bytes(name))


class FixedClock:
    def __init__(self, now: datetime = NOW) -> None:
        self._now = now

    def now(self) -> datetime:
        return self._now


class FakeHttp:
    """Maps exact URLs or URL prefixes (key ending in '*') to bytes or JSON values."""

    def __init__(self, routes: dict[str, Any] | None = None) -> None:
        self.routes: dict[str, Any] = dict(routes or {})
        self.calls: list[str] = []

    def _lookup(self, url: str) -> Any:
        self.calls.append(url)
        if url in self.routes:
            return self.routes[url]
        best = ""
        for key in self.routes:
            if key.endswith("*") and url.startswith(key[:-1]) and len(key) > len(best):
                best = key
        if best:
            return self.routes[best]
        raise KeyError(f"FakeHttp: no route for {url}")

    def get_bytes(self, url: str, *, accept: str | None = None) -> bytes:
        value = self._lookup(url)
        if isinstance(value, Exception):
            raise value
        if isinstance(value, bytes):
            return value
        return json.dumps(value).encode()

    def get_json(self, url: str) -> Any:
        value = self._lookup(url)
        if isinstance(value, Exception):
            raise value
        if isinstance(value, bytes):
            return json.loads(value)
        return value

    def post_json(self, url: str, payload: Any, headers: dict[str, str] | None = None) -> Any:
        raise AssertionError("sources must not POST")
