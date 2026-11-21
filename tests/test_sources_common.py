from __future__ import annotations

import time
from datetime import UTC, datetime, timedelta

from mybuddy.sources._common import (
    clean_snippet,
    clean_title,
    clip,
    from_struct_time,
    is_web_url,
    parse_iso,
    strip_html,
)


def test_strip_html() -> None:
    html = "<p>Hello&nbsp;<b>world</b> &amp; co</p><script>alert(1)</script><br>bye"
    assert strip_html(html) == "Hello world & co bye"
    assert strip_html("") == ""


def test_clip_caps_length() -> None:
    text = "word " * 200
    out = clip(text, 300)
    assert len(out) <= 300
    assert out.endswith("…")
    assert clip("short", 300) == "short"
    assert len(clean_title("x" * 500)) <= 200
    assert len(clean_snippet("<i>y</i> " * 400)) <= 300


def test_is_web_url() -> None:
    assert is_web_url("https://a.b/c")
    assert is_web_url("http://a.b/c")
    assert not is_web_url("javascript:alert(1)")
    assert not is_web_url("/relative")
    assert not is_web_url(None)


def test_parse_iso_and_struct_time_are_utc() -> None:
    dt = parse_iso("2026-11-20T10:00:00.000Z")
    assert dt == datetime(2026, 11, 20, 10, tzinfo=UTC)
    ist = parse_iso("2026-11-20T15:30:00+05:30")
    assert ist is not None and ist.tzinfo == UTC and ist.hour == 10
    assert parse_iso("garbage") is None
    assert parse_iso(None) is None
    st = time.strptime("2026-11-20 10:00:00", "%Y-%m-%d %H:%M:%S")
    assert from_struct_time(st) == datetime(2026, 11, 20, 10, tzinfo=UTC)
    assert from_struct_time("nope") is None
    naive = parse_iso("2026-11-20T10:00:00")
    assert naive is not None and naive.utcoffset() == timedelta(0)
