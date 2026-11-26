"""Small helpers shared by the source implementations."""

from __future__ import annotations

import calendar
import re
import time
from collections.abc import Iterable, Mapping
from datetime import UTC, datetime
from html.parser import HTMLParser
from typing import Any

from mybuddy.models import Item

TITLE_MAX = 200
SNIPPET_MAX = 300

_WS = re.compile(r"\s+")


class _TextExtractor(HTMLParser):
    _SKIP = frozenset({"script", "style", "noscript", "template"})

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._skip_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in self._SKIP:
            self._skip_depth += 1
        elif tag in {"br", "p", "div", "li", "tr", "h1", "h2", "h3", "h4"}:
            self.parts.append(" ")

    def handle_endtag(self, tag: str) -> None:
        if tag in self._SKIP and self._skip_depth:
            self._skip_depth -= 1

    def handle_data(self, data: str) -> None:
        if not self._skip_depth:
            self.parts.append(data)


def collapse(text: str) -> str:
    """Collapse runs of whitespace into single spaces and trim."""
    return _WS.sub(" ", text).strip()


def strip_html(text: str) -> str:
    """Return plain text from an HTML fragment (entities decoded, whitespace collapsed)."""
    if not text:
        return ""
    parser = _TextExtractor()
    parser.feed(text)
    parser.close()
    return collapse("".join(parser.parts))


def clip(text: str, limit: int) -> str:
    """Cut text to at most ``limit`` characters, on a word boundary when possible."""
    text = collapse(text)
    if len(text) <= limit:
        return text
    cut = text[: limit - 1]
    space = cut.rfind(" ")
    if space > limit // 2:
        cut = cut[:space]
    return cut.rstrip(" ,.;:-") + "…"


def clean_title(text: str) -> str:
    return clip(strip_html(text), TITLE_MAX)


def clean_snippet(text: str) -> str:
    return clip(strip_html(text), SNIPPET_MAX)


def is_web_url(url: object) -> bool:
    if not isinstance(url, str):
        return False
    lowered = url.strip().lower()
    return (lowered.startswith("https://") or lowered.startswith("http://")) and len(lowered) > 8


def to_utc(value: datetime) -> datetime:
    """Make a datetime timezone-aware UTC (naive values are assumed to be UTC)."""
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def parse_iso(value: object) -> datetime | None:
    """Parse an ISO-8601 timestamp or date (``Z`` suffix allowed); None if invalid."""
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    if text.endswith(("Z", "z")):
        text = text[:-1] + "+00:00"
    try:
        return to_utc(datetime.fromisoformat(text))
    except (ValueError, OverflowError):  # e.g. "0001-01-01T00:00:00+05:00" overflows in UTC
        return None


def from_struct_time(value: object) -> datetime | None:
    """Convert feedparser's UTC ``time.struct_time`` into an aware datetime."""
    if not isinstance(value, time.struct_time):
        return None
    try:
        return datetime.fromtimestamp(calendar.timegm(value), tz=UTC)
    except (OverflowError, ValueError, OSError):
        return None


def from_epoch(value: object) -> datetime | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    try:
        return datetime.fromtimestamp(float(value), tz=UTC)
    except (OverflowError, ValueError, OSError):
        return None


def newest_first(items: Iterable[Item]) -> list[Item]:
    """Sort items by published time, newest first; undated items go last (stable)."""
    floor = datetime.min.replace(tzinfo=UTC)
    return sorted(items, key=lambda it: it.published or floor, reverse=True)


# --- spec validation -------------------------------------------------------------------


def spec_str(spec: Mapping[str, Any], key: str) -> str:
    value = spec.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"source {spec.get('name')!r}: {key!r} must be a non-empty string")
    return value.strip()


def spec_str_list(spec: Mapping[str, Any], key: str) -> list[str]:
    value = spec.get(key)
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, list) or not value:
        raise ValueError(f"source {spec.get('name')!r}: {key!r} must be a non-empty list")
    out = [v.strip() for v in value if isinstance(v, str) and v.strip()]
    if len(out) != len(value):
        raise ValueError(f"source {spec.get('name')!r}: {key!r} must contain only strings")
    return out


def spec_number(spec: Mapping[str, Any], key: str, default: float, *, minimum: float = 0) -> float:
    value = spec.get(key, default)
    if isinstance(value, bool) or not isinstance(value, int | float) or value < minimum:
        raise ValueError(f"source {spec.get('name')!r}: {key!r} must be a number >= {minimum}")
    return float(value)


def spec_int(spec: Mapping[str, Any], key: str, default: int, *, minimum: int = 1) -> int:
    value = spec.get(key, default)
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise ValueError(f"source {spec.get('name')!r}: {key!r} must be an integer >= {minimum}")
    return value
