"""Per-visitor state and preferences: seen stories, theme, reduced motion, blocked storage."""

from __future__ import annotations

import json
import re

import pytest

pytest.importorskip("playwright")

from playwright.sync_api import expect  # noqa: E402
from ui_support import (  # noqa: E402
    DAY,
    TOPICS,
    N,
    bar_animations,
    bubble,
    list_item,
    make_reports,
    story_index,
    wait_story_index,
)

pytestmark = pytest.mark.ui

SEEN = re.compile(r"\bseen\b")


def _view(page, k):
    bubble(page, k).click()
    wait_story_index(page, k)
    page.keyboard.press("Escape")
    expect(page.locator("#story")).to_be_hidden()


def _assert_seen(page, seen_ks):
    for k in range(N):
        b = bubble(page, k)
        if k in seen_ks:
            expect(b).to_have_class(SEEN)
            expect(b).to_have_attribute("aria-label", f"{TOPICS[k][1]} story, seen")
        else:
            expect(b).not_to_have_class(SEEN)
            expect(b).to_have_attribute("aria-label", f"{TOPICS[k][1]} story")


# ---------------------------------------------------------------- seen state


def test_viewing_marks_seen_and_persists_across_reload(ui):
    page = ui.open()
    _assert_seen(page, set())
    _view(page, 1)
    _view(page, 4)
    _assert_seen(page, {1, 4})
    expect(page.locator("#bearBubble")).not_to_have_class(SEEN)

    page.reload()
    expect(page.locator("#hlList .hl-item")).to_have_count(N + 1)
    _assert_seen(page, {1, 4})
    stored = json.loads(page.evaluate("localStorage.getItem('mybuddy.seen.v2')"))
    assert stored["day"] == DAY
    assert set(stored["ids"]) == {TOPICS[1][0], TOPICS[4][0]}


def test_today_bubble_seen_when_all_viewed(ui):
    page = ui.open()
    page.locator("#bearBubble").click()
    for k in range(1, N):
        page.keyboard.press("ArrowRight")
        wait_story_index(page, k)
    page.keyboard.press("ArrowRight")  # past the end closes
    expect(page.locator("#story")).to_be_hidden()
    _assert_seen(page, set(range(N)))
    expect(page.locator("#bearBubble")).to_have_class(SEEN)


def test_seen_resets_when_generated_at_moves_to_a_new_day(ui):
    page = ui.open()
    _view(page, 0)
    _view(page, 2)
    _assert_seen(page, {0, 2})

    # Same day, but topic 2 got a fresh report: only that one becomes unseen.
    ui.site.write(make_reports(reports={2: {"created_at": f"{DAY}T08:12:00+05:30"}}))
    page.reload()
    expect(page.locator("#hlList .hl-item")).to_have_count(N + 1)
    _assert_seen(page, {0})

    # Next day's run: everything is new again.
    ui.site.write(make_reports(generated_at="2026-11-24T08:15:02+05:30"))
    page.reload()
    expect(page.locator("#hlList .hl-item")).to_have_count(N + 1)
    _assert_seen(page, set())


def test_corrupt_seen_storage_is_ignored(ui):
    ctx = ui.new_context(init_scripts=("localStorage.setItem('mybuddy.seen.v2', '{not json');",))
    page = ui.open(context=ctx)
    _assert_seen(page, set())
    _view(page, 3)
    _assert_seen(page, {3})


BLOCKED_STORAGE = {
    "methods-throw": """
        (() => {
          const boom = () => { throw new DOMException('blocked', 'SecurityError'); };
          for (const m of ['getItem', 'setItem', 'removeItem', 'clear', 'key'])
            Storage.prototype[m] = boom;
        })();
    """,
    "getter-throws": """
        Object.defineProperty(window, 'localStorage', {
          configurable: true,
          get() { throw new DOMException('blocked', 'SecurityError'); },
        });
    """,
}


@pytest.mark.parametrize("script", BLOCKED_STORAGE.values(), ids=BLOCKED_STORAGE.keys())
def test_works_when_storage_throws(ui, script):
    page = ui.open(init_scripts=(script,))
    expect(page.locator("#hlList .hl-item")).to_have_count(N + 1)
    _view(page, 2)
    _assert_seen(page, {2})  # kept in memory for this visit
    page.locator("#themeBtn").click()  # opens dark; switches to light (kept in memory)
    expect(page.locator("html")).to_have_attribute("data-mode", "light")
    list_item(page, 0).click()
    assert story_index(page) == 0


# ---------------------------------------------------------------- theme


def _mode(page):
    return page.evaluate(
        "() => [document.documentElement.getAttribute('data-theme'),"
        " document.documentElement.getAttribute('data-mode')]"
    )


DARK_BG = "rgb(12, 15, 30)"  # Ink & Periwinkle --bg (#0C0F1E)
LIGHT_BG = "rgb(246, 247, 252)"  # Ink & Periwinkle light --bg (#F6F7FC)


def _bg(page):
    return page.evaluate("getComputedStyle(document.body).backgroundColor")


@pytest.mark.parametrize("scheme", ["light", "dark", "no-preference"])
def test_opens_dark_by_default(ui, scheme):
    page = ui.open(color_scheme=scheme)
    assert _mode(page) == ["dark", "dark"]
    assert _bg(page) == DARK_BG
    expect(page.locator("#themeBtn")).to_have_attribute("aria-label", "Switch to light theme")
    expect(page.locator('meta[name="theme-color"]')).to_have_attribute("content", "#0C0F1E")
    # The system preference changing later does not flip the page either.
    page.emulate_media(color_scheme="light")
    assert _mode(page) == ["dark", "dark"]


def test_first_paint_is_dark(ui):
    """The inline head script alone sets dark + palette before CSS paints (no light flash)."""
    ui.allow_console(r"ERR_FAILED|Failed to load")
    ctx = ui.new_context(color_scheme="light")
    ctx.route("**/assets/app.js", lambda route: route.abort())  # head script only
    page = ui.open(context=ctx, wait=False)
    page.wait_for_load_state()
    assert page.evaluate(
        "() => [document.documentElement.dataset.theme, document.documentElement.dataset.palette]"
    ) == ["dark", "periwinkle"]
    assert _bg(page) == DARK_BG


def test_saved_light_theme_is_applied_before_first_paint(ui):
    ui.allow_console(r"ERR_FAILED|Failed to load")
    ctx = ui.new_context(
        color_scheme="dark", init_scripts=("localStorage.setItem('mybuddy.theme', 'light');",)
    )
    ctx.route("**/assets/app.js", lambda route: route.abort())  # head script only
    page = ui.open(context=ctx, wait=False)
    page.wait_for_load_state()
    assert page.evaluate("document.documentElement.dataset.theme") == "light"
    assert _bg(page) == LIGHT_BG


def test_garbage_saved_theme_falls_back_to_dark(ui):
    ctx = ui.new_context(init_scripts=("localStorage.setItem('mybuddy.theme', 'neon');",))
    page = ui.open(context=ctx)
    assert _mode(page) == ["dark", "dark"]


def test_toggle_choice_is_remembered(ui):
    page = ui.open(color_scheme="dark")
    btn = page.locator("#themeBtn")
    btn.click()
    expect(page.locator("html")).to_have_attribute("data-mode", "light")
    assert _mode(page) == ["light", "light"]
    expect(btn).to_have_attribute("aria-label", "Switch to dark theme")
    page.wait_for_function(
        "bg => getComputedStyle(document.body).backgroundColor === bg", arg=LIGHT_BG
    )
    expect(page.locator('meta[name="theme-color"]')).to_have_attribute("content", "#F6F7FC")
    assert page.evaluate("localStorage.getItem('mybuddy.theme')") == "light"

    # choose light -> reload -> still light
    page.reload()
    expect(page.locator("#hlList .hl-item")).to_have_count(N + 1)
    assert _mode(page) == ["light", "light"]
    assert _bg(page) == LIGHT_BG

    # switching back to dark is saved too
    page.locator("#themeBtn").click()
    expect(page.locator("html")).to_have_attribute("data-mode", "dark")
    assert page.evaluate("localStorage.getItem('mybuddy.theme')") == "dark"

    # clear storage -> dark default
    page.locator("#themeBtn").click()
    expect(page.locator("html")).to_have_attribute("data-mode", "light")
    page.evaluate("localStorage.clear()")
    page.reload()
    expect(page.locator("#hlList .hl-item")).to_have_count(N + 1)
    assert _mode(page) == ["dark", "dark"]
    assert _bg(page) == DARK_BG


@pytest.mark.parametrize("saved", [None, "light"])
def test_404_page_opens_dark(ui, saved):
    ui.allow_console(r"404")
    scripts = (f"localStorage.setItem('mybuddy.theme', '{saved}');",) if saved else ()
    page = ui.open("no/such/page", color_scheme="light", wait=False, init_scripts=scripts)
    expect(page.locator("h1")).to_have_text("Page not found")
    expect(page.locator("html")).to_have_attribute("data-theme", "dark")
    assert _bg(page) == DARK_BG
    expect(page.locator('meta[name="theme-color"]')).to_have_attribute("content", "#0C0F1E")
    # A light choice made on the home page is not carried over either.
    home = ui.open(color_scheme="light")
    home.locator("#themeBtn").click()
    expect(home.locator("html")).to_have_attribute("data-mode", "light")
    home.goto(ui.site.url("still/missing"))
    expect(home.locator("html")).to_have_attribute("data-theme", "dark")
    assert _bg(home) == DARK_BG


# ---------------------------------------------------------------- reduced motion


def test_reduced_motion_has_no_auto_advance(ui):
    page = ui.open(reduced_motion="reduce")
    bubble(page, 2).click()
    wait_story_index(page, 2)
    assert bar_animations(page) == []
    bars = page.locator("#storyBars .bar")
    assert [bars.nth(k).get_attribute("class") for k in range(N)] == (
        ["bar done"] * 3 + ["bar"] * (N - 3)
    )
    page.wait_for_timeout(500)
    assert story_index(page) == 2
    # Manual navigation still works and swaps slides instantly.
    page.keyboard.press("ArrowRight")
    wait_story_index(page, 3)
    expect(page.locator("#storyStage .slide")).to_have_count(1)
    page.keyboard.press("Escape")
    expect(page.locator("#story")).to_be_hidden()  # closes without animation


def test_reduced_motion_content_is_visible(ui):
    page = ui.open(reduced_motion="reduce")
    expect(page.locator("#deck .card.is-top")).to_be_visible()
    page.wait_for_timeout(300)
    hidden = page.evaluate(
        """() => {
            const sel = '.hero-title .w > span, .hero-sub, #bubbles li, #hlList li, #deck .card.is-top, '
                      + '#deck .card.is-top .card-hl, .steps li, .how-title';
            return [...document.querySelectorAll(sel)]
              .filter(n => parseFloat(getComputedStyle(n).opacity) < 0.99)
              .map(n => n.className || n.tagName);
        }"""
    )
    assert hidden == []
    # No long-running decorative animations are left going.
    infinite = page.evaluate(
        """() => document.getAnimations()
            .filter(a => a.effect.getComputedTiming().iterations === Infinity && a.playState === 'running')
            .map(a => a.animationName || 'anim')"""
    )
    assert infinite == []
    page.locator("#nextBtn").click()
    expect(page.locator("#deck .card.is-top")).to_have_attribute("data-i", "1")
