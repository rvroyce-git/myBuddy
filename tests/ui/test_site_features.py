"""Manager feedback: local times, palettes, "How it works" tooltips, "What stays, what leaves"."""

from __future__ import annotations

import re

import pytest

pytest.importorskip("playwright")

from playwright.sync_api import expect  # noqa: E402
from ui_support import (  # noqa: E402
    DAY,
    GENERATED_AT,
    TOPICS,
    N,
    due_text,
    ist_clock,
    list_item,
    local_stamp,
    make_reports,
)

pytestmark = pytest.mark.ui

IST_WORD = re.compile(r"\bIST\b")

# Every piece of text and every accessible label on the page, hidden or not.
ALL_TEXT_JS = """() => {
  const out = [document.title];
  const walk = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
  for (let n = walk.nextNode(); n; n = walk.nextNode()) {
    if (!n.parentElement.closest('script, style')) out.push(n.nodeValue);
  }
  document.querySelectorAll('*').forEach(el => {
    for (const a of ['aria-label', 'title', 'alt', 'aria-description']) {
      if (el.hasAttribute(a)) out.push(el.getAttribute(a));
    }
  });
  return out.join('\\n');
}"""


def _texts(page):
    return page.evaluate(ALL_TEXT_JS)


# ---------------------------------------------------------------- local time, no "IST"


@pytest.mark.parametrize(
    "tz,locale,now",
    [
        ("Europe/London", "en-GB", "2026-11-23T09:00:00+00:00"),
        ("America/New_York", "en-US", "2026-11-23T09:00:00-05:00"),
    ],
    ids=["london", "new-york"],
)
def test_times_follow_the_visitors_time_zone(ui, tz, locale, now):
    # Topic 3 (Mon–Fri 07:30 IST) has no report yet, so it shows its next run in local time.
    ui.site.write(make_reports(reports={3: None}))
    page = ui.open(timezone_id=tz, locale=locale, now=now)

    def stamp(iso):
        return local_stamp(iso, now=now, tz=tz, locale=locale)

    def sched(hm):
        return ist_clock(hm, now=now, tz=tz, locale=locale)

    for k, (_id, _t, _s, _e, _sch, hm) in enumerate(TOPICS):
        due = due_text("07:30", frozenset(range(5)), now=now, tz=tz, locale=locale)
        want = due if k == 3 else stamp(f"{DAY}T{hm}:11+05:30")
        expect(list_item(page, k).locator(".hl-when")).to_have_text(want)
        expect(page.locator(f"#deck .card[data-i='{k}'] .when")).to_have_text(want)
    expect(page.locator("#updatedText")).to_have_text(
        "Updated " + local_stamp(GENERATED_AT, now=now, tz=tz, locale=locale, mid=True)
    )
    expect(page.locator("#readyBy")).to_have_text(f"Ready by {sched('08:15')}.")
    expect(page.locator("#tip0")).to_contain_text(f"From {sched('06:30')} daily,")

    # Spot-check the conversions themselves.
    if tz == "Europe/London":
        assert sched("08:15") == "02:45" and stamp(f"{DAY}T06:30:11+05:30") == "01:00"
        # Monday 09:00 London: today's 07:30 IST run is long past -> Tuesday 02:00 London.
        assert due == "Due tomorrow · 02:00"
    else:  # New York is 10:30 behind IST: 06:30 IST on the 23rd is 8:00 PM on the 22nd
        assert (
            sched("08:15") == "9:45 PM" and stamp(f"{DAY}T06:30:11+05:30") == "Yesterday · 8:00 PM"
        )
        assert due == "Due 9:00 PM"  # Tuesday 07:30 IST = Monday 9:00 PM in New York

    # Story viewer shows the same local time.
    list_item(page, 0).click()
    expect(page.locator("#storyStage .slide").last.locator(".slide-when")).to_have_text(
        stamp(f"{DAY}T06:30:11+05:30")
    )
    assert not IST_WORD.search(_texts(page)), "a time zone label leaked into the page"


def test_no_ist_anywhere_on_the_page(ui):
    page = ui.open(now="2026-11-23T08:20:00+05:30")
    for k in range(N):
        list_item(page, k).click()
        expect(page.locator("#story")).to_be_visible()
        assert not IST_WORD.search(_texts(page))
        page.keyboard.press("Escape")
        expect(page.locator("#story")).to_be_hidden()
    assert not IST_WORD.search(_texts(page))
    assert "IST" not in page.locator("body").inner_text()


# ---------------------------------------------------------------- palettes

ACCENT_JS = (
    "getComputedStyle(document.documentElement).getPropertyValue('--accent').trim().toUpperCase()"
)


def test_default_palette_is_periwinkle(ui):
    page = ui.open()
    expect(page.locator("html")).to_have_attribute("data-palette", "periwinkle")
    assert page.evaluate(ACCENT_JS) == "#9DA8FF"


@pytest.mark.parametrize(
    "query,palette,accent",
    [
        ("mint", "mint", "#5EE6B5"),
        ("periwinkle", "periwinkle", "#9DA8FF"),
        ("MiNt", "mint", "#5EE6B5"),
    ],
)
def test_palette_query_switches_palette(ui, query, palette, accent):
    page = ui.open(f"?palette={query}")
    expect(page.locator("html")).to_have_attribute("data-palette", palette)
    assert page.evaluate(ACCENT_JS) == accent
    expect(page.locator("html")).to_have_attribute("data-theme", "dark")


@pytest.mark.parametrize(
    "query",
    [
        "neon",
        "",
        "%22%3E%3Cimg%20src%3Dx%20onerror%3Dwindow.__xss%3D1%3E",
        "honey%22%20onload%3D1",
        "mint;background:red",
        "x" * 500,
    ],
    ids=["unknown", "empty", "markup", "attr-break", "css", "long"],
)
def test_unknown_or_malicious_palette_is_ignored(ui, query):
    page = ui.open(f"?palette={query}")
    expect(page.locator("html")).to_have_attribute("data-palette", "periwinkle")
    assert page.evaluate(ACCENT_JS) == "#9DA8FF"
    assert page.evaluate("window.__xss ?? null") is None
    assert page.evaluate("document.querySelectorAll('img').length") == 0


def test_topics_share_one_card_colour(ui):
    page = ui.open()
    bgs = page.evaluate(
        """() => [...document.querySelectorAll('#deck .card:not(.skeleton) .card-in')]
              .map(n => getComputedStyle(n).backgroundImage.replace(/\\d+deg,\\s*/, ''))"""
    )
    assert len(bgs) == N and len(set(bgs)) == 1, bgs
    faces = page.evaluate(
        "() => [...document.querySelectorAll('#bubbles .bubble .face')]"
        ".map(n => getComputedStyle(n).backgroundImage.replace(/\\d+deg,\\s*/, ''))"
    )
    assert len(set(faces)) == 1, faces


# ---------------------------------------------------------------- "How it works" tooltips

STEPS = "#steps .step"


def _tip_state(page, k):
    return page.evaluate(
        """k => {
            const t = document.getElementById('tip' + k), r = t.getBoundingClientRect();
            return {open: t.classList.contains('open'), vis: getComputedStyle(t).visibility,
                    left: r.left, right: r.right, top: r.top, bottom: r.bottom,
                    vw: document.documentElement.clientWidth, vh: innerHeight};
        }""",
        k,
    )


def _wait_open(page, k, is_open=True):
    tip = page.locator(f"#tip{k}")
    if is_open:
        expect(tip).to_have_class(re.compile(r"\bopen\b"))
        expect(tip).to_be_visible()
        expect(tip).to_have_css("opacity", "1")
    else:
        expect(tip).not_to_have_class(re.compile(r"\bopen\b"))
        expect(tip).to_be_hidden()


def test_steps_are_buttons_described_by_tooltips(ui):
    page = ui.open()
    steps = page.locator(STEPS)
    expect(steps).to_have_count(4)
    for k in range(4):
        expect(steps.nth(k)).to_have_attribute("aria-describedby", f"tip{k}")
        tip = page.locator(f"#tip{k}")
        expect(tip).to_have_attribute("role", "tooltip")
        assert len(re.findall(r"[\w~:'’.-]*\w", tip.text_content())) <= 25
        _wait_open(page, k, False)
        box = steps.nth(k).bounding_box()
        assert box["width"] >= 44 and box["height"] >= 44
    expect(page.get_by_role("button", name="AI summarises")).to_have_accessible_description(
        re.compile(r"^Groq AI writes")
    )


def test_tooltip_on_hover(ui):
    page = ui.open()
    step = page.locator(STEPS).nth(1)
    step.scroll_into_view_if_needed()
    step.hover()
    _wait_open(page, 1)
    page.locator(".flow-title").hover()
    _wait_open(page, 1, False)


def test_tooltip_on_keyboard_focus_and_escape(ui):
    page = ui.open()
    page.locator(STEPS).nth(1).focus()  # programmatic focus isn't :focus-visible; tab in instead
    page.keyboard.press("Shift+Tab")
    page.keyboard.press("Tab")
    expect(page.locator(STEPS).nth(1)).to_be_focused()
    _wait_open(page, 1)
    page.keyboard.press("Tab")
    _wait_open(page, 1, False)
    _wait_open(page, 2)
    page.keyboard.press("Escape")
    _wait_open(page, 2, False)
    expect(page.locator(STEPS).nth(2)).to_be_focused()
    page.keyboard.press("Enter")  # Enter toggles it back open, and closed again
    _wait_open(page, 2)
    page.keyboard.press("Enter")
    _wait_open(page, 2, False)


def test_tooltip_on_tap_toggles_and_closes_outside(ui):
    page = ui.open(viewport={"width": 390, "height": 844}, has_touch=True, is_mobile=True)
    steps = page.locator(STEPS)
    steps.nth(2).scroll_into_view_if_needed()
    steps.nth(2).tap()
    _wait_open(page, 2)
    steps.nth(2).tap()
    _wait_open(page, 2, False)
    steps.nth(0).tap()
    _wait_open(page, 0)
    steps.nth(3).tap()  # another step: only one open at a time
    _wait_open(page, 0, False)
    _wait_open(page, 3)
    page.locator(".flow-title").tap()
    _wait_open(page, 3, False)
    steps.nth(1).tap()
    _wait_open(page, 1)
    page.keyboard.press("Escape")
    _wait_open(page, 1, False)


@pytest.mark.parametrize("width", [360, 390, 1024])
def test_tooltips_stay_inside_the_viewport(ui, width):
    page = ui.open(viewport={"width": width, "height": 740}, has_touch=True)
    for k in range(4):
        step = page.locator(STEPS).nth(k)
        step.scroll_into_view_if_needed()
        step.tap()
        _wait_open(page, k)
        st = _tip_state(page, k)
        assert st["left"] >= 0 and st["right"] <= st["vw"], (k, st)
        assert st["top"] >= 0 and st["bottom"] <= st["vh"], (k, st)
        step.tap()
        _wait_open(page, k, False)
    sw = page.evaluate("document.documentElement.scrollWidth")
    assert sw <= width


def test_tooltip_flips_below_near_the_top(ui):
    page = ui.open(viewport={"width": 390, "height": 844}, has_touch=True)
    page.evaluate(
        "() => { const r = document.querySelector('#steps').getBoundingClientRect();"
        " scrollBy(0, r.top - 70); }"
    )
    page.locator(STEPS).nth(0).tap()
    _wait_open(page, 0)
    expect(page.locator("#tip0")).to_have_class(re.compile(r"\bbelow\b"))
    st = _tip_state(page, 0)
    step_bottom = page.locator(STEPS).nth(0).bounding_box()
    assert st["top"] >= step_bottom["y"] + step_bottom["height"]


def test_tooltip_respects_reduced_motion(ui):
    page = ui.open(reduced_motion="reduce")
    step = page.locator(STEPS).nth(0)
    step.scroll_into_view_if_needed()
    step.hover()
    _wait_open(page, 0)
    assert page.locator("#tip0").evaluate("t => getComputedStyle(t).transform") == "none"


# ---------------------------------------------------------------- "What stays, what leaves"


def test_privacy_section_renders(ui):
    page = ui.open()
    flow = page.locator("#flow")
    expect(flow.get_by_role("heading", name="What stays, what leaves")).to_be_attached()
    expect(flow.locator(".flow-card")).to_have_count(2)
    expect(flow.locator(".flow-list li")).to_have_count(6)
    expect(flow.locator(".stays h3")).to_have_text("Stays on our server")
    expect(flow.locator(".leaves h3")).to_have_text("Leaves the server")
    for li in flow.locator(".flow-list li").all():
        assert len(li.inner_text().replace("→", " ").split()) <= 10
    assert len(flow.locator(".flow-note").text_content().split()) <= 20
    # It sits between "How it works" and the footer.
    order = page.evaluate(
        "() => ['.how', '#flow', '.foot'].map(s => document.querySelector(s)"
        ".getBoundingClientRect().top + scrollY)"
    )
    assert order == sorted(order)
    flow.scroll_into_view_if_needed()
    expect(flow).to_have_class(re.compile(r"\bin\b"))
    expect(flow.locator(".flow-list li").last).to_have_css("opacity", "1")
    expect(flow.locator(".flow-card.leaves")).to_have_css("opacity", "1")


def test_privacy_section_side_by_side_on_desktop_stacked_on_mobile(ui):
    page = ui.open()
    page.locator("#flow").scroll_into_view_if_needed()
    a, b = (page.locator(f"#flow .flow-card.{c}").bounding_box() for c in ("stays", "leaves"))
    assert abs(a["y"] - b["y"]) < 2 and b["x"] > a["x"] + a["width"]

    for width in (390, 360):
        page.set_viewport_size({"width": width, "height": 800})
        a, b = (page.locator(f"#flow .flow-card.{c}").bounding_box() for c in ("stays", "leaves"))
        assert abs(a["x"] - b["x"]) < 2 and b["y"] >= a["y"] + a["height"]
        assert a["x"] >= 16 and a["x"] + a["width"] <= width - 16 + 1
        assert page.evaluate("document.documentElement.scrollWidth") <= width


def test_privacy_section_visible_with_reduced_motion(ui):
    page = ui.open(reduced_motion="reduce")
    hidden = page.evaluate(
        """() => [...document.querySelectorAll('#flow .flow-card, #flow li, #flow .flow-note')]
              .filter(n => parseFloat(getComputedStyle(n).opacity) < 0.99).length"""
    )
    assert hidden == 0


# ---------------------------------------------------------------- English words, locale clock


@pytest.mark.parametrize(
    "tz,locale",
    [("Asia/Tokyo", "ja-JP"), ("Europe/Berlin", "de-DE")],
    ids=["ja-JP", "de-DE"],
)
def test_other_locales_keep_english_words_and_24h_clock(ui, tz, locale):
    now = "2026-11-24T09:00:00+05:30"  # the default reports are from the day before
    ui.site.write(make_reports(reports={3: None}))
    page = ui.open(timezone_id=tz, locale=locale, now=now)
    upd = "Updated " + local_stamp(GENERATED_AT, now=now, tz=tz, locale=locale, mid=True)
    expect(page.locator("#updatedText")).to_have_text(upd)
    for k, (*_, hm) in enumerate(TOPICS):
        if k == 3:
            continue
        expect(list_item(page, k).locator(".hl-when")).to_have_text(
            local_stamp(f"{DAY}T{hm}:11+05:30", now=now, tz=tz, locale=locale)
        )
    expect(list_item(page, 3).locator(".hl-when")).to_have_text(
        due_text("07:30", frozenset(range(5)), now=now, tz=tz, locale=locale)
    )
    times = page.evaluate(
        "() => [...document.querySelectorAll('.hl-when, .when, #updatedText, #readyBy, #tip0')]"
        ".map(n => n.textContent).join('\\n')"
    )
    assert re.search(r"\b\d{2}:\d{2}\b", times), times  # 24-hour, 2-digit hours
    assert not re.search(r"AM|PM", times), times
    assert all(ord(c) < 0x2000 or c in "·—" for c in times), times  # no 昨日 / 11月 / Gestern
    assert "Gestern" not in times and "Uhr" not in times


# ---------------------------------------------------------------- topics without a report


@pytest.mark.parametrize(
    "tz,locale,now,want",
    [
        # Monday 00:30 London = 06:00 IST: today's 07:30 IST run is still ahead.
        ("Europe/London", "en-GB", "2026-11-23T00:30:00+00:00", "Due 02:00"),
        # Monday 10:00 IST: the run already happened -> tomorrow's.
        ("Asia/Kolkata", "en-US", "2026-11-23T10:00:00+05:30", "Due tomorrow · 7:30 AM"),
        # Saturday: a Mon–Fri topic is back on Monday.
        ("Europe/London", "en-GB", "2026-11-28T10:00:00+00:00", "Back Monday"),
    ],
    ids=["due-today", "due-tomorrow", "back-monday"],
)
def test_topic_without_report_shows_its_next_run(ui, tz, locale, now, want):
    ui.site.write(make_reports(reports={3: None}))  # deep dive: "Mon–Fri · 07:30 IST"
    page = ui.open(timezone_id=tz, locale=locale, now=now)
    assert due_text("07:30", frozenset(range(5)), now=now, tz=tz, locale=locale) == want
    expect(list_item(page, 3).locator(".hl-when")).to_have_text(want)
    expect(page.locator("#deck .card[data-i='3'] .when")).to_have_text(want)


def test_hourly_topic_without_report_shows_no_time(ui):
    ui.site.write(make_reports(reports={1: None}, topics={1: {"schedule": "Every hour"}}))
    page = ui.open(now="2026-11-23T08:20:00+05:30")
    expect(list_item(page, 1).locator(".hl-when")).to_have_count(0)


# ---------------------------------------------------------------- contrast in every palette

CONTRAST_JS = """() => {
  const probe = document.createElement('i');
  document.body.appendChild(probe);
  const rgb = (v) => {
    probe.style.color = ''; probe.style.color = 'var(' + v + ')';
    return getComputedStyle(probe).color.match(/[\\d.]+/g).slice(0, 3).map(Number);
  };
  const lum = ([r, g, b]) => {
    const f = (c) => { c /= 255; return c <= 0.04045 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4; };
    return 0.2126 * f(r) + 0.7152 * f(g) + 0.0722 * f(b);
  };
  const cr = (a, b) => { const x = lum(rgb(a)), y = lum(rgb(b)); return (Math.max(x, y) + 0.05) / (Math.min(x, y) + 0.05); };
  const out = {
    'ink/bg': cr('--ink', '--bg'), 'ink/surface': cr('--ink', '--surface-solid'),
    'ink-2/bg': cr('--ink-2', '--bg'), 'ink-2/surface': cr('--ink-2', '--surface-solid'),
    'card-ink/card-1': cr('--card-ink', '--card-1'), 'card-ink/card-2': cr('--card-ink', '--card-2'),
    'accent-ink/accent': cr('--accent-ink', '--accent'), 'oc-ink/oc': cr('--oc-ink', '--oc'),
    'mark/bg': cr('--mark', '--bg'), 'oc/card-1': cr('--oc', '--card-1'), 'oc/card-2': cr('--oc', '--card-2'),
  };
  // Elements drawn on the card gradient really use the on-card accent.
  const oc = getComputedStyle(probe).color, want = (probe.style.color = 'var(--oc)', getComputedStyle(probe).color);
  const used = ['#hlList .hl-ico', '#deck .card .tag .ico-wrap', '#deck .card .orb']
    .map(s => getComputedStyle(document.querySelector(s)).color === want);
  probe.remove();
  return {ratios: out, used, oc};
}"""
TEXT_PAIRS = (
    "ink/bg",
    "ink/surface",
    "ink-2/bg",
    "ink-2/surface",
    "card-ink/card-1",
    "card-ink/card-2",
    "accent-ink/accent",
    "oc-ink/oc",
)  # text: >= 4.5
MARK_PAIRS = ("mark/bg", "oc/card-1", "oc/card-2")  # icons, bars, thin marks: >= 3


@pytest.mark.parametrize("theme", ["dark", "light"])
@pytest.mark.parametrize("palette", ["honey", "mint", "periwinkle"])
def test_palette_contrast(ui, palette, theme):
    page = ui.open(f"?palette={palette}")
    if theme == "light":
        page.locator("#themeBtn").click()
        expect(page.locator("html")).to_have_attribute("data-mode", "light")
        page.wait_for_timeout(800)  # let the view transition finish
    res = page.evaluate(CONTRAST_JS)
    low = {k: round(v, 2) for k, v in res["ratios"].items() if v < (4.5 if k in TEXT_PAIRS else 3)}
    assert low == {}, (palette, theme, res["ratios"])
    assert set(TEXT_PAIRS + MARK_PAIRS) == set(res["ratios"])
    assert res["used"] == [True, True, True], res
    page.locator("#hlList .hl-item").first.click()
    bar = page.evaluate(
        "() => getComputedStyle(document.querySelector('#storyBars .bar i')).backgroundColor"
    )
    want = page.evaluate(
        "() => { const i = document.createElement('i'); i.style.color = 'var(--oc)';"
        " document.body.appendChild(i); const c = getComputedStyle(i).color; i.remove(); return c; }"
    )
    assert bar == want


# ---------------------------------------------------------------- small polish


def test_headline_meta_never_cuts_the_topic_at_1280(ui):
    page = ui.open(now="2026-11-24T08:20:00+05:30")  # every report is "Yesterday · …" here
    expect(list_item(page, 4).locator(".hl-when")).to_have_text(re.compile(r"^Yesterday · "))
    cut = page.evaluate(
        """() => [...document.querySelectorAll('#hlList .hl-item')].flatMap(item => {
            const box = item.querySelector('.hl-body')?.getBoundingClientRect();
            return [...item.querySelectorAll('.hl-topic, .hl-when')].filter(n =>
                n.scrollWidth > n.clientWidth + 1 || n.getBoundingClientRect().right > box.right + 1
            ).map(n => n.textContent);
        })"""
    )
    assert cut == []


def test_feed_count_matches_config(ui):
    import yaml  # noqa: PLC0415
    from conftest import SITE_DIR  # noqa: PLC0415

    cfg = yaml.safe_load((SITE_DIR.parent / "config" / "topics.yaml").read_text(encoding="utf-8"))
    count = 0
    for t in cfg["topics"]:
        count += len(t.get("sources") or [])
        for day in (t.get("rotation") or {}).values():
            count += len(day.get("sources") or [])
    page = ui.open()
    expect(page.locator("#tip0")).to_contain_text(f"checks {count} public feeds")


def test_skip_link_is_a_44px_target(ui):
    page = ui.open()
    page.keyboard.press("Tab")
    skip = page.locator(".skip")
    expect(skip).to_be_focused()
    page.wait_for_timeout(300)
    box = skip.bounding_box()
    assert box["height"] >= 44 and box["width"] >= 44
