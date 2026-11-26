"""Data builders and page-state helpers shared by the website UI tests."""

from __future__ import annotations

import copy
import re
from datetime import date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

# (id, title, short label shown under the bubble, emoji, schedule, created_at HH:MM IST)
TOPICS: list[tuple[str, str, str, str, str, str]] = [
    ("ai_news", "AI news", "AI news", "🧠", "Daily · 06:30 IST", "06:30"),
    ("security", "Cybersecurity alerts", "Security", "🛡️", "Daily · 07:00 IST", "07:00"),
    ("github_trending", "Trending GitHub repos", "GitHub", "⭐", "Daily · 07:15 IST", "07:15"),
    ("deep_dive", "Deep dive", "Deep dive", "🔎", "Mon–Fri · 07:30 IST", "07:30"),
    ("research_papers", "AI research papers", "Papers", "📄", "Daily · 07:45 IST", "07:45"),
    ("startups", "Startup & product launches", "Launches", "🚀", "Daily · 08:00 IST", "08:00"),
    ("ai_models", "New open-source AI models", "Models", "🧩", "Daily · 08:15 IST", "08:10"),
]
N = len(TOPICS)
GENERATED_AT = "2026-11-23T08:15:02+05:30"
DAY = "2026-11-23"

# ---------------------------------------------------------------- visitor-local times
# The page shows every time in the visitor's zone + locale, never with a zone label.
VISITOR_TZ = "America/Los_Angeles"  # conftest default; deliberately not IST
NOW = "2026-11-23T08:20:00+05:30"  # a frozen "now" just after the daily run (= Nov 22 PST)
IST = ZoneInfo("Asia/Kolkata")
_MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


H24_LOCALES = {"en-GB", "de-DE", "ja-JP", "fr-FR"}  # locales whose default clock is 24-hour


def clock_text(dt: datetime, locale: str = "en-US") -> str:
    """The page's clock: English, with only the 12/24-hour choice taken from the locale."""
    if locale in H24_LOCALES:
        return f"{dt.hour:02d}:{dt.minute:02d}"
    h = dt.hour % 12 or 12
    return f"{h}:{dt.minute:02d} {'AM' if dt.hour < 12 else 'PM'}"


def _date_text(d: date) -> str:
    return f"{_MONTHS[d.month - 1]} {d.day}"  # always English, e.g. "Nov 21"


def local_stamp(
    iso: str, now: str = NOW, tz: str = VISITOR_TZ, locale: str = "en-US", mid: bool = False
) -> str:
    """A report time: '5:00 PM', 'Yesterday · 5:00 PM' or 'Nov 21 · 5:00 PM'.

    ``mid`` is the in-phrase form used after "Updated" ('yesterday · 5:00 PM')."""
    zone = ZoneInfo(tz)
    dt = datetime.fromisoformat(iso).astimezone(zone)
    today = datetime.fromisoformat(now).astimezone(zone).date()
    text = clock_text(dt, locale)
    if dt.date() == today:
        return text
    if dt.date() == today - timedelta(days=1):
        return ("yesterday · " if mid else "Yesterday · ") + text
    return _date_text(dt.date()) + " · " + text


def ist_clock(hm: str, now: str = NOW, tz: str = VISITOR_TZ, locale: str = "en-US") -> str:
    """A scheduled HH:MM IST time on today's IST date, as the visitor's local clock time."""
    ist_day = datetime.fromisoformat(now).astimezone(IST).date()
    h, m = map(int, hm.split(":"))
    dt = datetime(ist_day.year, ist_day.month, ist_day.day, h, m, tzinfo=IST).astimezone(
        ZoneInfo(tz)
    )
    return clock_text(dt, locale)


def due_text(
    hm: str,
    weekdays: frozenset[int] = frozenset(range(7)),
    now: str = NOW,
    tz: str = VISITOR_TZ,
    locale: str = "en-US",
) -> str:
    """Label for a topic without a report: its next run ('Due 5:45 PM', 'Due tomorrow · 5:45 PM'
    or 'Back Monday'). ``weekdays`` uses Python numbering (Mon=0); a run up to 60 min late
    still counts as due today."""
    zone = ZoneInfo(tz)
    now_dt = datetime.fromisoformat(now)
    h, m = map(int, hm.split(":"))
    ist_day = now_dt.astimezone(IST).date()
    for k in range(8):
        d = ist_day + timedelta(days=k)
        if d.weekday() not in weekdays:
            continue
        at = datetime(d.year, d.month, d.day, h, m, tzinfo=IST)
        if at + timedelta(minutes=60) <= now_dt:
            continue
        gap = (at.astimezone(zone).date() - now_dt.astimezone(zone).date()).days
        if gap <= 0:
            return "Due " + clock_text(at.astimezone(zone), locale)
        if gap == 1:
            return "Due tomorrow · " + clock_text(at.astimezone(zone), locale)
        return "Back " + at.astimezone(zone).strftime("%A")
    return ""


def headline(k: int) -> str:
    return f"Headline {k + 1}: {TOPICS[k][1]} story of the day."


def make_report(k: int, day: str = DAY) -> dict[str, Any]:
    tid, title, *_ = TOPICS[k]
    return {
        "created_at": f"{day}T{TOPICS[k][5]}:11+05:30",
        "headline": headline(k),
        "summary": f"First sentence about {title}. Second sentence here! Third one is dropped.",
        "items": [
            {
                "title": f"{title} item {j + 1}",
                "url": f"https://example.com/{tid}/{j + 1}",
                "source": f"Source {j + 1}",
                "blurb": "A blurb.",
                "published": f"{day}T01:02:03+00:00",
            }
            for j in range(4)
        ],
    }


def make_reports(
    generated_at: str | None = GENERATED_AT,
    topics: dict[int, dict[str, Any] | None] | None = None,
    reports: dict[int, dict[str, Any] | None] | None = None,
) -> dict[str, Any]:
    """Build a reports.json document with 7 topics.

    ``topics`` maps index -> keys merged into the topic (``None`` drops the topic);
    ``reports`` maps index -> keys merged into the report (``None`` makes the report null).
    """
    out: list[dict[str, Any]] = []
    for k, (tid, title, _short, emoji, schedule, _hm) in enumerate(TOPICS):
        topic: dict[str, Any] = {
            "id": tid,
            "title": title,
            "emoji": emoji,
            "description": f"About {title}.",
            "schedule": schedule,
            "report": make_report(k),
        }
        if reports and k in reports:
            over = reports[k]
            topic["report"] = None if over is None else {**topic["report"], **copy.deepcopy(over)}
        if topics and k in topics:
            tover = topics[k]
            if tover is None:
                continue
            topic.update(copy.deepcopy(tover))
        out.append(topic)
    doc: dict[str, Any] = {"topics": out}
    if generated_at is not None:
        doc["generated_at"] = generated_at
    return doc


# ---------------------------------------------------------------- page state


def deck_index(page: Any) -> int:
    return int(page.locator("#deck .card.is-top").get_attribute("data-i"))


def settle_deck(page: Any) -> None:
    """Wait until the opening deal (JS timer ~1.4 s + CSS transitions) has finished."""
    page.wait_for_function(
        """() => {
            const cards = [...document.querySelectorAll('#deck .card:not(.skeleton)')];
            return cards.length > 0 && cards.every(c => !c.style.transitionDelay
                && c.getAnimations().length === 0);
        }""",
        polling=100,
    )


def current_dot(page: Any) -> int:
    return int(
        page.evaluate(
            "() => [...document.querySelectorAll('#dots .dot')]"
            ".findIndex(d => d.getAttribute('aria-current') === 'true')"
        )
    )


def story_open(page: Any) -> bool:
    return not page.locator("#story").evaluate("n => n.hidden")


def story_index(page: Any) -> int:
    """Index (0-based) of the newest slide in the story viewer."""
    label = page.evaluate(
        "() => document.querySelector('#storyStage').lastElementChild?.getAttribute('aria-label')"
    )
    m = re.match(r"(\d+) of \d+$", label or "")
    assert m, f"no slide is showing (aria-label={label!r})"
    return int(m.group(1)) - 1


def story_title(page: Any) -> str:
    return str(
        page.evaluate(
            "() => document.querySelector('#storyStage').lastElementChild"
            ".querySelector('.slide-name').textContent"
        )
    )


def wait_story_index(page: Any, k: int) -> None:
    page.wait_for_function(
        "k => document.querySelector('#storyStage').lastElementChild"
        "?.getAttribute('aria-label')?.startsWith((k + 1) + ' of ')",
        arg=k,
    )


# The story timer is the CSS `fillBar` animation on the active progress bar (STORY_MS = 12 s);
# its `animationend` advances the story. Playwright's page.clock fakes JS timers only, not the
# document timeline, so the tests drive the timer through the Web Animations API instead.
BAR_ANIMS_JS = """() => document.getAnimations()
  .filter(a => a.animationName === 'fillBar' && a.playState !== 'finished' && a.playState !== 'idle')"""


def bar_animations(page: Any) -> list[dict[str, Any]]:
    return list(
        page.evaluate(
            f"""() => ({BAR_ANIMS_JS})().map(a => ({{
                state: a.playState,
                duration: a.effect.getComputedTiming().duration,
                bar: [...document.querySelectorAll('#storyBars .bar')].indexOf(a.effect.target.parentNode),
            }}))"""
        )
    )


def seek_story_timer(page: Any, ms: float) -> None:
    """Move every running story-timer animation to ``ms`` milliseconds."""
    page.evaluate(f"ms => ({BAR_ANIMS_JS})().forEach(a => {{ a.currentTime = ms; }})", ms)


def bubble(page: Any, k: int) -> Any:
    return page.locator("#bubbles li.topic .bubble").nth(k)


def list_item(page: Any, k: int) -> Any:
    return page.locator("#hlList li:not(.hl-all-li) .hl-item").nth(k)
