"""The swipeable card deck: buttons, keyboard, dots, wrap-around and mouse dragging."""

from __future__ import annotations

import pytest

pytest.importorskip("playwright")

from playwright.sync_api import expect  # noqa: E402
from ui_support import (  # noqa: E402
    TOPICS,
    N,
    current_dot,
    deck_index,
    settle_deck,
    story_index,
)

pytestmark = pytest.mark.ui


def _at(page, k):
    expect(page.locator("#deck .card.is-top")).to_have_attribute("data-i", str(k))
    assert current_dot(page) == k
    expect(page.locator("#deckLive")).to_have_text(f"{k + 1} of {N}: {TOPICS[k][1]}")
    # Only the top card is focusable / exposed.
    assert page.locator("#deck .card[tabindex='0']").count() == 1
    assert page.locator("#deck .card[aria-hidden='false']").count() == 1


def test_next_and_prev_buttons_wrap_around(ui):
    page = ui.open()
    _at(page, 0)
    page.locator("#prevBtn").click()
    _at(page, N - 1)
    page.locator("#nextBtn").click()
    _at(page, 0)
    for k in range(1, N):
        page.locator("#nextBtn").click()
        _at(page, k)
    page.locator("#nextBtn").click()
    _at(page, 0)
    expect(page.locator("#story")).to_be_hidden()


def test_keyboard_arrows_move_the_deck(ui):
    page = ui.open()
    page.locator("body").focus()
    page.keyboard.press("ArrowRight")
    page.keyboard.press("ArrowRight")
    _at(page, 2)
    page.keyboard.press("ArrowLeft")
    _at(page, 1)
    page.keyboard.press("ArrowLeft")
    page.keyboard.press("ArrowLeft")
    _at(page, N - 1)
    # Modified arrows are left to the browser.
    page.keyboard.press("Alt+ArrowRight")
    _at(page, N - 1)


def test_dots_jump_to_a_card(ui):
    page = ui.open()
    dots = page.locator("#dots .dot")
    expect(dots).to_have_count(N)
    expect(dots.nth(4)).to_have_attribute("aria-label", f"Show {TOPICS[4][1]}")
    dots.nth(4).click()
    _at(page, 4)
    dots.nth(5).click()  # adjacent: animated "next"
    _at(page, 5)
    dots.nth(4).click()  # adjacent: animated "prev"
    _at(page, 4)
    dots.nth(4).click()  # current: no-op
    _at(page, 4)
    dots.nth(0).click()
    _at(page, 0)


def test_top_card_opens_story_on_click_and_enter(ui):
    page = ui.open()
    settle_deck(page)
    page.locator("#nextBtn").click()
    settle_deck(page)
    page.locator("#deck .card.is-top").click()
    expect(page.locator("#story")).to_be_visible()
    assert story_index(page) == 1
    page.keyboard.press("Escape")
    expect(page.locator("#story")).to_be_hidden()

    page.locator("#deck .card.is-top").focus()
    page.keyboard.press("Enter")
    expect(page.locator("#story")).to_be_visible()
    assert story_index(page) == 1


def _drag(page, dx, steps):
    box = page.locator("#deck .card.is-top").bounding_box()
    x, y = box["x"] + box["width"] / 2, box["y"] + box["height"] / 2
    page.mouse.move(x, y)
    page.mouse.down()
    for s in range(1, steps + 1):
        page.mouse.move(x + dx * s / steps, y + 2 * s / steps)
    page.mouse.up()
    return box["width"]


@pytest.mark.parametrize("direction", [-1, 1], ids=["left", "right"])
def test_drag_past_threshold_advances(ui, direction):
    page = ui.open()
    settle_deck(page)
    width = _drag(page, direction * 260, 12)
    assert width * 0.3 < 260  # beyond the "far" threshold
    _at(page, 1)
    expect(page.locator("#story")).to_be_hidden()
    settle_deck(page)
    _drag(page, direction * 260, 12)
    _at(page, 2)


def test_small_drag_springs_back(ui):
    page = ui.open()
    settle_deck(page)
    _drag(page, -24, 6)  # moved (> 6px) but short and slow: no swipe, no tap
    _at(page, 0)
    expect(page.locator("#story")).to_be_hidden()
    top = page.locator("#deck .card.is-top")
    expect(top).not_to_have_class("card is-top dragging")
    assert "dragging" not in (top.get_attribute("class") or "")
    expect(top).to_have_attribute("style", _contains("translate3d(0px, 0px, 0px)"))
    # Followers were put back too.
    assert page.locator("#deck .card.follow").count() == 0


def _contains(text):
    import re

    return re.compile(re.escape(text))


def test_drag_before_deal_finishes_is_ignored(ui):
    ctx = ui.new_context()
    page = ui.watch(ctx.new_page())
    page.clock.install(time="2026-11-23T03:00:00Z")
    page.clock.pause_at("2026-11-23T03:00:01Z")
    page.goto(ui.site.url())
    expect(page.locator("#deck .card.is-top")).to_have_count(1)
    # The deal timer (1.4 s) has not fired: dragging must not move or open anything.
    _drag(page, -260, 8)
    assert deck_index(page) == 0
    expect(page.locator("#story")).to_be_hidden()
    page.clock.run_for(1500)
    settle_deck(page)
    _drag(page, -260, 8)
    _at(page, 1)
