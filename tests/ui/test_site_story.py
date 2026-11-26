"""The full-screen story viewer."""

from __future__ import annotations

import re

import pytest

pytest.importorskip("playwright")

from playwright.sync_api import expect  # noqa: E402
from ui_support import (  # noqa: E402
    TOPICS,
    N,
    bar_animations,
    bubble,
    deck_index,
    list_item,
    seek_story_timer,
    settle_deck,
    story_index,
    story_open,
    story_title,
    wait_story_index,
)

pytestmark = pytest.mark.ui

STORY_MS = 12000


def _showing(page, k):
    expect(page.locator("#story")).to_be_visible()
    wait_story_index(page, k)
    assert story_title(page) == TOPICS[k][1]
    expect(page.locator("#storyLive")).to_contain_text(f"Story {k + 1} of {N}. {TOPICS[k][1]}.")


def _close_with_escape(page):
    page.keyboard.press("Escape")
    expect(page.locator("#story")).to_be_hidden()


# ---------------------------------------------------------------- opening


def test_opens_from_each_bubble(ui):
    page = ui.open()
    for k in (0, 3, N - 1):
        bubble(page, k).click()
        _showing(page, k)
        _close_with_escape(page)


def test_opens_from_headline_list(ui):
    page = ui.open()
    list_item(page, 5).click()
    _showing(page, 5)


def test_opens_from_card(ui):
    page = ui.open()
    settle_deck(page)
    page.locator("#dots .dot").nth(3).click()
    settle_deck(page)
    page.locator("#deck .card.is-top").click()
    _showing(page, 3)


@pytest.mark.parametrize("trigger", ["#bearBubble", "#hlList .hl-all"], ids=["today", "play-all"])
def test_play_all_starts_at_first_unseen(ui, trigger):
    page = ui.open()
    page.locator(trigger).click()
    _showing(page, 0)  # nothing seen yet
    page.keyboard.press("ArrowRight")
    _showing(page, 1)
    _close_with_escape(page)
    page.locator(trigger).click()
    _showing(page, 2)
    _close_with_escape(page)
    page.locator(trigger).click()
    _showing(page, 3)  # 0-2 are seen now, so it opens at the first unseen one
    for k in range(4, N):
        page.keyboard.press("ArrowRight")
        _showing(page, k)
    _close_with_escape(page)
    # Once everything is seen, it starts from the beginning.
    expect(page.locator("#bearBubble")).to_have_class(re.compile(r"\bseen\b"))
    page.locator(trigger).click()
    _showing(page, 0)


# ---------------------------------------------------------------- closing


def test_escape_closes_and_returns_focus_to_opener(ui):
    page = ui.open()
    bubble(page, 2).focus()
    page.keyboard.press("Enter")
    _showing(page, 2)
    expect(page.locator("#storyClose")).to_be_focused()
    _close_with_escape(page)
    expect(bubble(page, 2)).to_be_focused()


def test_close_button_closes_and_returns_focus(ui):
    page = ui.open()
    list_item(page, 4).focus()
    page.keyboard.press("Enter")
    _showing(page, 4)
    page.locator("#storyClose").click()
    expect(page.locator("#story")).to_be_hidden()
    expect(list_item(page, 4)).to_be_focused()
    assert page.locator("#storyStage").inner_html() == ""


def test_closing_syncs_deck_to_last_story_and_focuses_card(ui):
    page = ui.open()
    settle_deck(page)
    page.locator("#deck .card.is-top").focus()
    page.keyboard.press("Enter")
    _showing(page, 0)
    page.keyboard.press("ArrowRight")
    page.keyboard.press("ArrowRight")
    _showing(page, 2)
    _close_with_escape(page)
    assert deck_index(page) == 2
    expect(page.locator("#deck .card.is-top")).to_be_focused()


def test_backdrop_click_closes(ui):
    page = ui.open()
    bubble(page, 1).click()
    _showing(page, 1)
    page.mouse.click(10, 450)  # left edge: backdrop, outside the frame
    expect(page.locator("#story")).to_be_hidden()


# ---------------------------------------------------------------- navigation


def test_arrow_keys_and_side_buttons_navigate(ui):
    page = ui.open()
    bubble(page, 1).click()
    _showing(page, 1)
    page.keyboard.press("ArrowRight")
    _showing(page, 2)
    page.keyboard.press("ArrowLeft")
    page.keyboard.press("ArrowLeft")
    _showing(page, 0)
    page.keyboard.press("ArrowLeft")  # stays on the first story
    _showing(page, 0)
    page.locator("#storyNext").click()
    _showing(page, 1)
    page.locator("#storyPrev").click()
    _showing(page, 0)
    # The deck underneath did not move while the story was open.
    _close_with_escape(page)
    assert deck_index(page) == 0


def test_next_on_last_story_closes(ui):
    page = ui.open()
    bubble(page, N - 1).click()
    _showing(page, N - 1)
    expect(page.locator("#sticker")).to_have_class(re.compile(r"\bwave\b"))
    page.keyboard.press("ArrowRight")
    expect(page.locator("#story")).to_be_hidden()
    assert deck_index(page) == N - 1


def test_progress_bars_track_position(ui):
    page = ui.open()
    bubble(page, 3).click()
    _showing(page, 3)
    bars = page.locator("#storyBars .bar")
    expect(bars).to_have_count(N)
    classes = [bars.nth(k).get_attribute("class") for k in range(N)]
    assert classes[:3] == ["bar done"] * 3
    assert classes[3] == "bar run"
    assert classes[4:] == ["bar"] * (N - 4)


# ---------------------------------------------------------------- timer


def test_auto_advances_after_twelve_seconds(ui):
    page = ui.open()
    bubble(page, 0).click()
    _showing(page, 0)
    anims = bar_animations(page)
    assert anims == [{"state": "running", "duration": STORY_MS, "bar": 0}]

    seek_story_timer(page, STORY_MS - 1000)
    page.wait_for_timeout(250)
    assert story_index(page) == 0  # not yet

    seek_story_timer(page, STORY_MS - 20)
    _showing(page, 1)
    assert bar_animations(page) == [{"state": "running", "duration": STORY_MS, "bar": 1}]


def test_auto_advance_on_last_story_closes(ui):
    page = ui.open()
    bubble(page, N - 1).click()
    _showing(page, N - 1)
    seek_story_timer(page, STORY_MS - 20)
    expect(page.locator("#story")).to_be_hidden()
    assert bar_animations(page) == []


def test_hover_pauses_and_leaving_resumes(ui):
    page = ui.open()
    bubble(page, 0).click()
    _showing(page, 0)
    page.wait_for_timeout(700)  # let the opening animation finish
    page.locator("#storyStage .slide").last.locator(".slide-title").hover()
    expect(page.locator("#storyFrame")).to_have_class("story-frame paused")
    assert [a["state"] for a in bar_animations(page)] == ["paused"]
    seek_story_timer(page, STORY_MS - 1000)
    page.wait_for_timeout(1200)  # a running timer would have finished by now
    assert story_index(page) == 0

    page.mouse.move(5, 5)  # leave the story body
    expect(page.locator("#storyFrame")).to_have_class("story-frame")
    assert [a["state"] for a in bar_animations(page)] == ["running"]
    _showing(page, 1)  # the remaining ~1 s ran out


def test_pause_button_toggles(ui):
    page = ui.open()
    bubble(page, 0).click()
    _showing(page, 0)
    btn = page.locator("#storyPause")
    expect(btn).to_have_attribute("aria-pressed", "false")
    btn.click()
    expect(btn).to_have_attribute("aria-pressed", "true")
    expect(btn).to_have_attribute("aria-label", "Play stories")
    page.mouse.move(5, 5)
    assert [a["state"] for a in bar_animations(page)] == ["paused"]
    btn.click()
    expect(btn).to_have_attribute("aria-pressed", "false")
    assert [a["state"] for a in bar_animations(page)] == ["running"]
    # Reopening resets the paused state.
    btn.click()
    _close_with_escape(page)
    bubble(page, 1).click()
    expect(btn).to_have_attribute("aria-pressed", "false")


def test_repeated_open_close_does_not_stack_timers(ui):
    page = ui.open()
    for k in range(3):
        bubble(page, k % 3).click()
        _showing(page, k % 3)
        page.keyboard.press("ArrowRight")
        _close_with_escape(page)
    bubble(page, 0).click()
    _showing(page, 0)
    assert len(bar_animations(page)) == 1

    for step in range(1, 4):
        seek_story_timer(page, STORY_MS - 20)
        _showing(page, step)
        page.wait_for_timeout(300)
        assert story_index(page) == step  # exactly one step per 12 s
        assert len(bar_animations(page)) == 1


# ---------------------------------------------------------------- page lock


def test_body_scroll_locked_while_open(ui):
    page = ui.open(viewport={"width": 1280, "height": 700})
    page.evaluate("scrollTo(0, 300)")
    # function form: a bare expression string would need eval(), which the CSP blocks
    page.wait_for_function("() => scrollY === 300")
    list_item(page, 6).click()
    _showing(page, 6)
    lock = page.evaluate(
        "() => [document.documentElement.classList.contains('locked'),"
        " getComputedStyle(document.documentElement).overflowY, getComputedStyle(document.body).overflowY]"
    )
    assert lock == [True, "hidden", "hidden"]
    page.mouse.wheel(0, 600)
    page.wait_for_timeout(200)
    assert page.evaluate("scrollY") == 300
    # Everything behind the dialog is inert.
    assert page.evaluate(
        "() => ['#nav', '#main', '.foot', '.skip'].every(s => document.querySelector(s).inert)"
    )

    _close_with_escape(page)
    state = page.evaluate(
        "() => [document.documentElement.classList.contains('locked'),"
        " getComputedStyle(document.documentElement).overflowY, scrollY,"
        " ['#nav', '#main', '.foot', '.skip'].some(s => document.querySelector(s).inert)]"
    )
    assert state == [False, "visible", 300, False]
    page.mouse.wheel(0, 200)
    page.wait_for_function("() => scrollY > 300")


def test_focus_is_trapped_in_dialog(ui):
    page = ui.open()
    bubble(page, 0).click()
    _showing(page, 0)
    page.wait_for_timeout(700)
    inside = []
    for _ in range(12):
        page.keyboard.press("Tab")
        inside.append(
            page.evaluate("() => document.getElementById('story').contains(document.activeElement)")
        )
    for _ in range(4):
        page.keyboard.press("Shift+Tab")
        inside.append(
            page.evaluate("() => document.getElementById('story').contains(document.activeElement)")
        )
    assert all(inside), inside
    assert story_open(page)
