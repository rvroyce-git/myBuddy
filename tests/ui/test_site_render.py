"""Rendering from data: happy path, empty reports, load failures, hostile data, long text."""

from __future__ import annotations

import pytest

pytest.importorskip("playwright")

from playwright.sync_api import expect  # noqa: E402
from ui_support import (  # noqa: E402
    DAY,
    GENERATED_AT,
    NOW,
    TOPICS,
    N,
    due_text,
    headline,
    list_item,
    local_stamp,
    make_reports,
    settle_deck,
    story_index,
    story_title,
)

pytestmark = pytest.mark.ui

HL_ITEMS = "#hlList li:not(.hl-all-li) .hl-item"
CARDS = "#deck .card:not(.skeleton)"


def test_renders_every_topic_from_data(ui):
    page = ui.open(now=NOW)

    bubbles = page.locator("#bubbles li.topic .bubble")
    expect(bubbles).to_have_count(N)
    expect(page.locator(HL_ITEMS)).to_have_count(N)
    expect(page.locator(CARDS)).to_have_count(N)
    expect(page.locator("#dots .dot")).to_have_count(N)
    expect(page.locator("#deck .card.skeleton")).to_have_count(0)

    for k, (_id, title, short, _e, _s, hm) in enumerate(TOPICS):
        expect(bubbles.nth(k).locator(".label")).to_have_text(short)
        expect(bubbles.nth(k)).to_have_attribute("aria-label", f"{title} story")

        item = list_item(page, k)
        expect(item.locator(".hl-topic")).to_have_text(title)
        when = local_stamp(f"{DAY}T{hm}:11+05:30")  # visitor's zone (Los Angeles), no label
        expect(item.locator(".hl-when")).to_have_text(when)
        expect(item.locator(".hl-text")).to_have_text(headline(k))
        expect(item).to_have_attribute("aria-label", f"{title}: {headline(k)} Open story")

        card = page.locator(f"#deck .card[data-i='{k}']")
        expect(card.locator(".tag-name")).to_have_text(title)
        expect(card.locator(".when")).to_have_text(when)
        expect(card.locator(".card-hl")).to_have_text(headline(k))
        expect(card.locator(".num")).to_have_text(f"{k + 1:02d}")

    expect(page.locator("#updated")).to_be_visible()
    expect(page.locator("#updatedText")).to_have_text(
        "Updated " + local_stamp(GENERATED_AT, mid=True)
    )
    assert local_stamp(GENERATED_AT) == "6:45 PM"  # sanity: 08:15 IST = 18:45 PST
    expect(page.locator("#hlList .hl-all")).to_have_text("Play all")
    expect(page.locator("#deck .card.is-top")).to_have_attribute("data-i", "0")
    expect(page.locator("#loadErr")).to_be_hidden()
    # Fonts/CDN were stubbed out and everything still rendered.
    assert any("fonts.googleapis.com" in u for u in ui.external)


def test_times_are_local_with_day_when_not_the_visitors_today(ui):
    now = "2026-11-23T20:30:00-08:00"  # visitor (Los Angeles): Mon 23 Nov, 8:30 PM
    data = make_reports(
        generated_at="2026-11-23T02:45:02Z",  # = 08:15 IST = Sun 22 Nov 6:45 PM PST
        reports={
            0: {"created_at": "2026-11-23T10:00:00+00:00"},  # 2:00 AM PST today
            1: {"created_at": "2026-11-21T18:20:00+00:00"},  # Sat 21 Nov 10:20 AM PST
        },
    )
    ui.site.write(data)
    page = ui.open(now=now)
    expect(page.locator("#updatedText")).to_have_text("Updated yesterday · 6:45 PM")
    expect(list_item(page, 0).locator(".hl-when")).to_have_text("2:00 AM")
    expect(list_item(page, 1).locator(".hl-when")).to_have_text("Nov 21 · 10:20 AM")
    # Default report: 07:15 IST on 23 Nov = 22 Nov 5:45 PM PST.
    expect(list_item(page, 2).locator(".hl-when")).to_have_text("Yesterday · 5:45 PM")
    expect(page.locator("#deck .card[data-i='1'] .when")).to_have_text("Nov 21 · 10:20 AM")


def test_missing_generated_at_hides_updated_pill(ui):
    ui.site.write(make_reports(generated_at=None))
    page = ui.open()
    expect(page.locator(HL_ITEMS)).to_have_count(N)
    expect(page.locator("#updated")).to_be_hidden()


def test_story_shows_summary_and_three_links(ui):
    page = ui.open()
    list_item(page, 2).click()
    slide = page.locator("#storyStage .slide").last
    expect(slide.locator(".slide-title")).to_have_text(headline(2))
    expect(slide.locator(".slide-sum")).to_have_text(
        f"First sentence about {TOPICS[2][1]}. Second sentence here!"
    )
    chips = slide.locator("a.chip")
    expect(chips).to_have_count(3)
    expect(chips.nth(0).locator(".chip-src")).to_have_text("Source 1")
    expect(chips.nth(0).locator(".chip-title")).to_have_text(f"{TOPICS[2][1]} item 1")


# ---------------------------------------------------------------- report: null


def test_null_report_shows_empty_state_everywhere(ui):
    k = 2
    ui.site.write(make_reports(reports={k: None}))
    page = ui.open(now=NOW)
    title = TOPICS[k][1]

    expect(page.locator("#bubbles li.topic .bubble")).to_have_count(N)
    expect(page.locator("#bubbles li.topic .bubble").nth(k)).to_have_attribute(
        "aria-label", f"{title} story"
    )

    item = list_item(page, k)
    expect(item.locator(".hl-text")).to_have_text("Coming soon")
    expect(item.locator(".hl-text")).to_have_class("hl-text muted")
    # From the schedule: today's 07:15 IST run has passed (NOW = 08:20 IST), so the next one is
    # tomorrow 07:15 IST = 5:45 PM tomorrow in Los Angeles.
    assert due_text("07:15") == "Due tomorrow · 5:45 PM"
    expect(item.locator(".hl-when")).to_have_text(due_text("07:15"))

    card = page.locator(f"#deck .card[data-i='{k}']")
    expect(card.locator(".card-hl")).to_have_text("Coming soon")
    expect(card.locator(".card-hl")).to_have_class("card-hl muted")
    expect(card.locator(".when")).to_have_text(due_text("07:15"))

    item.click()
    assert story_index(page) == k
    assert story_title(page) == title
    slide = page.locator("#storyStage .slide").last
    expect(slide.locator(".slide-title")).to_have_text("Coming soon")
    expect(slide.locator(".slide-sum")).to_have_count(0)
    expect(slide.locator("a.chip")).to_have_count(0)
    # Neighbours are unaffected.
    expect(list_item(page, k + 1).locator(".hl-text")).to_have_text(headline(k + 1))


def test_all_reports_null_still_renders(ui):
    ui.site.write(make_reports(reports=dict.fromkeys(range(N))))
    page = ui.open()
    expect(page.locator(HL_ITEMS)).to_have_count(N)
    expect(page.locator(f"{HL_ITEMS} .hl-text")).to_have_text(["Coming soon"] * N)
    page.locator("#bearBubble").click()
    assert story_index(page) == 0
    page.keyboard.press("Escape")
    expect(page.locator("#story")).to_be_hidden()


def test_partial_report_fields_are_tolerated(ui):
    data = make_reports(
        reports={
            0: {"headline": "", "summary": None, "items": None},
            1: {"items": [None, {"url": "https://example.com/x"}, 7, "https://example.com/y"]},
            2: {"created_at": "not a date"},
        },
        topics={3: {"title": None}, 4: {"id": None}},  # topic without id is skipped
    )
    ui.site.write(data)
    page = ui.open(now=NOW)
    expect(page.locator(HL_ITEMS)).to_have_count(N - 1)
    expect(list_item(page, 0).locator(".hl-text")).to_have_text("Coming soon")
    expect(list_item(page, 2).locator(".hl-when")).to_have_text(due_text("07:15"))
    expect(list_item(page, 3).locator(".hl-topic")).to_have_text("deep_dive")
    list_item(page, 1).click()
    expect(page.locator("#storyStage .slide").last.locator("a.chip")).to_have_count(0)


# ---------------------------------------------------------------- load failures


def test_missing_reports_falls_back_to_sample(ui):
    ui.allow_console(r"404")
    ui.site.remove("reports.json")
    page = ui.open()
    expect(page.locator("#loadErr")).to_be_hidden()
    expect(page.locator(HL_ITEMS)).to_have_count(N)
    expect(list_item(page, 0).locator(".hl-text")).to_contain_text("Sample")
    expect(page.locator("#updated")).to_be_visible()


def test_unreachable_reports_falls_back_to_sample(ui):
    ui.allow_console(r"ERR_FAILED|Failed to load")
    ctx = ui.new_context()
    ctx.route("**/data/reports.json", lambda route: route.abort())
    page = ui.open(context=ctx)
    expect(page.locator("#loadErr")).to_be_hidden()
    expect(list_item(page, 0).locator(".hl-text")).to_contain_text("Sample")


def test_invalid_json_shows_friendly_error_and_retry_recovers(ui):
    ui.site.write("{ not json", "reports.json")
    page = ui.open()
    err = page.locator("#loadErr")
    expect(err).to_be_visible()
    expect(err).to_contain_text("Couldn't load today's news.")
    expect(page.locator(HL_ITEMS)).to_have_count(0)
    expect(page.locator("#deck .shimmer")).to_have_count(0)
    # Nothing to open, and nothing throws.
    page.locator("#bearBubble").click()
    page.locator("#nextBtn").click()
    page.keyboard.press("ArrowRight")
    expect(page.locator("#story")).to_be_hidden()

    ui.site.write(make_reports())
    page.get_by_role("button", name="Retry").click()
    expect(page.locator(HL_ITEMS)).to_have_count(N)
    expect(err).to_be_hidden()
    expect(page.locator("#deck .card.skeleton")).to_have_count(0)


@pytest.mark.parametrize(
    "payload",
    ['{"topics": []}', '{"topics": "nope"}', "null", "[]"],
    ids=["no-topics", "topics-not-list", "null", "array"],
)
def test_unusable_data_shows_error(ui, payload):
    ui.site.write(payload, "reports.json")
    page = ui.open()
    expect(page.locator("#loadErr")).to_be_visible()
    expect(page.locator(HL_ITEMS)).to_have_count(0)


def test_both_data_files_missing_shows_error(ui):
    ui.allow_console(r"404")
    ui.site.remove("reports.json")
    ui.site.remove("reports.sample.json")
    page = ui.open()
    expect(page.locator("#loadErr")).to_be_visible()
    expect(page.locator(HL_ITEMS)).to_have_count(0)
    expect(page.locator("#updated")).to_be_hidden()


# ---------------------------------------------------------------- hostile data

XSS = [
    "<script>window.__xss=1</script>",
    '<img src=x onerror="window.__xss=2">',
    "<b>bold</b><svg onload=window.__xss=3>",
    '"><iframe src=javascript:window.__xss=4>',
]


def _hostile_data():
    def bad(k: int) -> str:
        return f"{XSS[k % len(XSS)]} t{k}"

    reports = {
        k: {
            "headline": bad(k) + ".",
            "summary": bad(k + 1) + ". " + bad(k + 2) + ".",
            "items": [
                {"title": bad(k + 3), "url": "https://example.com/ok", "source": bad(k)},
                {"title": "alert", "url": "javascript:alert(1)", "source": "x"},
            ],
        }
        for k in range(N)
    }
    topics = {
        k: {"title": bad(k), "emoji": bad(k + 1), "schedule": bad(k + 2), "description": bad(k)}
        for k in range(N)
    }
    topics[6]["id"] = '<img src=x onerror="window.__xss=5">'  # unknown id -> emoji path
    return make_reports(topics=topics, reports=reports, generated_at='<img src=x onerror="1">')


def _assert_no_injected_markup(page):
    found = page.evaluate(
        """() => {
            const scopes = ['#bubbles', '#deck', '#hlList', '#storyStage', '#deckLive', '#storyLive'];
            const bad = [];
            for (const s of scopes) {
              document.querySelector(s).querySelectorAll('script, img, iframe, b, svg[onload]')
                .forEach(n => bad.push(s + ' ' + n.outerHTML.slice(0, 60)));
            }
            return {bad, xss: window.__xss ?? null};
        }"""
    )
    assert found == {"bad": [], "xss": None}


def test_html_in_data_renders_as_text(ui):
    ui.site.write(_hostile_data())
    page = ui.open()
    expect(page.locator(HL_ITEMS)).to_have_count(N)
    expect(list_item(page, 2).locator(".hl-topic")).to_have_text(f"{XSS[2]} t2")
    expect(page.locator("#deck .card[data-i='1'] .tag-name")).to_have_text(f"{XSS[1]} t1")
    _assert_no_injected_markup(page)

    for k in range(N):
        list_item(page, k).click()
        assert story_index(page) == k
        slide = page.locator("#storyStage .slide").last
        expect(slide.locator(".slide-title")).to_have_text(f"{XSS[k % 4]} t{k}.")
        expect(slide.locator("a.chip")).to_have_count(1)
        _assert_no_injected_markup(page)
        page.keyboard.press("Escape")
        expect(page.locator("#story")).to_be_hidden()
    # The unknown-id topic shows its emoji text, never as markup.
    expect(page.locator("#bubbles li.topic").nth(6).locator(".emoji")).to_have_text(f"{XSS[3]} t7")
    _assert_no_injected_markup(page)


def _links_for(ui, items):
    ui.site.write(make_reports(reports={0: {"items": items}}))
    page = ui.open()
    list_item(page, 0).click()
    chips = page.locator("#storyStage .slide").last.locator("a.chip")
    return page, chips


def test_unsafe_scheme_links_are_dropped(ui):
    page, chips = _links_for(
        ui,
        [
            {"title": "js", "url": "javascript:window.__xss=9"},
            {"title": "js2", "url": "  JaVaScRiPt:window.__xss=9"},
            {"title": "data", "url": "data:text/html,<script>window.__xss=9</script>"},
            {"title": "vb", "url": "vbscript:msgbox(1)"},
            {"title": "file", "url": "file:///etc/passwd"},
            {"title": "ok", "url": "https://example.com/safe"},
        ],
    )
    expect(chips).to_have_count(1)
    expect(chips).to_have_attribute("href", "https://example.com/safe")
    hrefs = page.evaluate("() => [...document.querySelectorAll('#story a')].map(a => a.href)")
    assert all(h.startswith("https://") for h in hrefs), hrefs


def test_relative_and_http_links_are_dropped(ui):
    _page, chips = _links_for(
        ui,
        [
            {"title": "relative", "url": "/myBuddy/evil"},
            {"title": "dot-relative", "url": "evil/page"},
            {"title": "protocol-relative", "url": "//evil.example.com/x"},
            {"title": "plain http", "url": "http://example.com/insecure"},
            {"title": "no host", "url": "https:///nohost"},
            {"title": "not a string", "url": {"href": "https://example.com/obj"}},
            {"title": "ok", "url": "https://example.com/safe"},
        ],
    )
    expect(chips).to_have_count(1)
    expect(chips).to_have_attribute("href", "https://example.com/safe")


def test_item_without_url_is_dropped(ui):
    page, chips = _links_for(
        ui,
        [
            {"title": "no url at all"},
            {"title": "empty url", "url": ""},
            {"title": "null url", "url": None},
            {"title": "blank", "url": "   "},
        ],
    )
    expect(chips).to_have_count(0)
    assert page.evaluate("() => document.querySelectorAll('#story a').length") == 0


def test_external_links_open_safely_in_new_tab(ui):
    page = ui.open()
    list_item(page, 0).click()
    links = page.locator("#story a[href]")
    expect(links).to_have_count(3)
    for a in links.all():
        assert a.get_attribute("href").startswith("https://")
        assert a.get_attribute("target") == "_blank"
        assert set(a.get_attribute("rel").split()) >= {"noopener", "noreferrer"}
    page.keyboard.press("Escape")
    for a in page.locator("a[target='_blank']").all():
        assert set(a.get_attribute("rel").split()) >= {"noopener", "noreferrer"}


# ---------------------------------------------------------------- long text

LONG_HEADLINE = " ".join(["Extraordinarily"] + ["long words keep coming here"] * 40) + "."
UNBROKEN = "Supercalifragilistic" * 20


def _assert_no_horizontal_overflow(page):
    res = page.evaluate(
        """() => {
            const W = document.documentElement.clientWidth;
            scrollTo(9999, scrollY);
            const sx = scrollX;
            scrollTo(0, scrollY);
            const sel = '.hl-item, .hl-text, .hl-meta, #deck .card.is-top, .bubbles, .stage, '
                      + '.headlines, .hero, .foot, .nav, #storyStage .slide-title, #storyStage .chip';
            const wide = [...document.querySelectorAll(sel)]
              .filter(n => n.getClientRects().length)
              .map(n => [n.className, Math.round(n.getBoundingClientRect().right)])
              .filter(([, r]) => r > W + 1);
            return {sx, wide, sw: document.documentElement.scrollWidth, W};
        }"""
    )
    assert res["sx"] == 0, res
    assert res["wide"] == [], res
    assert res["sw"] <= res["W"], res


def test_long_text_does_not_scroll_sideways_at_360px(ui):
    reports = {
        0: {
            "headline": LONG_HEADLINE,
            "items": [
                {"title": UNBROKEN, "url": "https://example.com/" + "a" * 300, "source": UNBROKEN}
            ],
        },
        1: {"headline": UNBROKEN},
    }
    topics = {0: {"title": "A really very long topic title " * 4}, 1: {"title": UNBROKEN}}
    ui.site.write(make_reports(reports=reports, topics=topics))
    page = ui.open(viewport={"width": 360, "height": 740})
    settle_deck(page)
    _assert_no_horizontal_overflow(page)

    # Headlines are clamped to 3 lines with an ellipsis; the full text is kept.
    for node in (
        list_item(page, 0).locator(".hl-text"),
        page.locator("#deck .card[data-i='0'] .card-hl"),
    ):
        info = node.evaluate(
            """n => ({text: n.textContent, full: n.dataset.full, h: n.offsetHeight,
                      lh: parseFloat(getComputedStyle(n).lineHeight)})"""
        )
        assert info["full"] == LONG_HEADLINE
        assert info["text"].endswith("…") and len(info["text"]) < len(LONG_HEADLINE)
        assert info["h"] <= info["lh"] * 3 + 2, info

    list_item(page, 0).click()
    expect(page.locator("#story")).to_be_visible()
    _assert_no_horizontal_overflow(page)
    page.keyboard.press("Escape")
    list_item(page, 1).click()
    _assert_no_horizontal_overflow(page)


def test_headline_clamp_survives_resize(ui):
    ui.site.write(make_reports(reports={0: {"headline": LONG_HEADLINE}}))
    page = ui.open(viewport={"width": 360, "height": 740})
    node = list_item(page, 0).locator(".hl-text")
    narrow = node.text_content()
    page.set_viewport_size({"width": 1280, "height": 900})
    page.wait_for_function(
        "t => document.querySelectorAll('#hlList .hl-text')[0].textContent !== t", arg=narrow
    )
    wide = node.text_content()
    assert wide != narrow
    assert node.get_attribute("data-full") == LONG_HEADLINE
