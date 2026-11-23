"""Hardening: broken or hostile reports.json, missing browser APIs, stalled loads, the
Content-Security-Policy, and page copy rules."""

from __future__ import annotations

import base64
import hashlib
import json
import re

import pytest

pytest.importorskip("playwright")

from conftest import SITE_DIR  # noqa: E402
from playwright.sync_api import expect  # noqa: E402
from ui_support import (  # noqa: E402
    NOW,
    TOPICS,
    N,
    headline,
    list_item,
    make_reports,
    story_index,
)

pytestmark = pytest.mark.ui

HL_ITEMS = "#hlList li:not(.hl-all-li) .hl-item"


def _exercise(page):
    """Open a story, step through it, close it, flip the deck and pick a topic: nothing may throw."""
    page.locator("#bearBubble").click()
    page.keyboard.press("ArrowRight")
    page.keyboard.press("Escape")
    expect(page.locator("#story")).to_be_hidden()
    page.locator("#nextBtn").click()
    page.locator("#subscribe").scroll_into_view_if_needed()
    chips = page.locator("#subsChips .tchip:not(.tc-all)")
    if chips.count():
        chips.first.click()


# ---------------------------------------------------------------- broken data variants


def _variant(name):
    good = make_reports()
    if name == "report-null":
        return make_reports(reports=dict.fromkeys(range(N)))
    if name == "report-wrong-types":
        return make_reports(
            reports={
                0: {"headline": {"x": 1}, "summary": ["a", "b"], "items": "nope"},
                1: {"headline": 42, "summary": 7, "created_at": 1700000000000},
                2: {"items": [{"title": {"t": 1}, "url": "https://example.com/a"}, [], True]},
                3: {"created_at": None},
                4: {"created_at": True, "headline": None},
            }
        )
    if name == "report-is-array":
        return make_reports(reports={0: None}) | {
            "topics": [{**good["topics"][0], "report": ["x"]}] + good["topics"][1:]
        }
    if name == "topics-mixed-junk":
        return {
            "generated_at": None,
            "topics": [None, 1, "ai_news", [], {"id": {"x": 1}}, {"id": ""}, *good["topics"]],
        }
    if name == "topic-fields-wrong-types":
        return make_reports(
            topics={
                0: {"title": ["a"], "emoji": {"e": 1}, "schedule": 5, "bit": "zero"},
                1: {"title": 12345},
                2: {"schedule": None, "report": None},
            }
        )
    if name == "prototype-ids":
        return make_reports(
            topics={
                0: {"id": "constructor"},
                2: {"id": "__proto__"},
                3: {"id": "toString"},
                4: {"id": "hasOwnProperty"},
            }
        )
    raise AssertionError(name)


VARIANTS = [
    "report-null",
    "report-wrong-types",
    "report-is-array",
    "topics-mixed-junk",
    "topic-fields-wrong-types",
    "prototype-ids",
]


@pytest.mark.parametrize("name", VARIANTS)
def test_broken_data_renders_without_errors(ui, name):
    data = _variant(name)
    ctx = ui.new_context()
    ctx.route(
        "**/data/reports.json",
        lambda route: route.fulfill(
            status=200, content_type="application/json", body=json.dumps(data)
        ),
    )
    page = ui.open(context=ctx, now=NOW)
    expect(page.locator("#loadErr")).to_be_hidden()
    assert page.locator(HL_ITEMS).count() >= 1
    text = page.locator("main").inner_text()
    assert "[object Object]" not in text
    assert "undefined" not in text and "NaN" not in text
    assert "1970" not in text and "Dec 31" not in text and "Jan 1 " not in text
    _exercise(page)
    # ui.check() (conftest) fails the test on any page error or console error


def test_wrong_typed_report_fields_fall_back_cleanly(ui):
    ui.site.write(_variant("report-wrong-types"))
    page = ui.open(now=NOW)
    expect(list_item(page, 0).locator(".hl-text")).to_have_text("Coming soon")
    expect(list_item(page, 1).locator(".hl-text")).to_have_text("42")
    # a numeric or null created_at is not a date: the topic shows its next run instead of 1970
    for k in (1, 3, 4):
        expect(list_item(page, k).locator(".hl-when")).not_to_have_text(re.compile("Dec 31|Jan 1"))
    list_item(page, 2).click()
    expect(page.locator("#storyStage .slide").last.locator("a.chip")).to_have_count(0)


def test_null_generated_at_hides_updated_pill(ui):
    ui.site.write(make_reports() | {"generated_at": None})
    page = ui.open()
    expect(page.locator(HL_ITEMS)).to_have_count(N)
    expect(page.locator("#updated")).to_be_hidden()


def test_prototype_named_topic_ids_render_as_plain_topics(ui):
    ui.site.write(_variant("prototype-ids"))
    page = ui.open()
    expect(page.locator(HL_ITEMS)).to_have_count(N)
    expect(list_item(page, 0).locator(".hl-topic")).to_have_text(TOPICS[0][1])
    labels = page.locator("#bubbles li.topic .label").all_inner_texts()
    assert all("function" not in s and "native code" not in s for s in labels), labels
    # unknown ids have no fixed subscription bit -> no chip; only Security brings the bolt
    expect(page.locator("#subsChips .tchip:not(.tc-all)")).to_have_count(N - 4)
    expect(page.locator("#subsChips .tc-bolt")).to_have_count(1)
    expect(page.locator("#subsChips .tchip[data-id='security'] .tc-bolt")).to_have_count(1)


def test_duplicate_topic_ids_are_shown_once(ui):
    data = make_reports()
    data["topics"].append({**data["topics"][0], "title": "Duplicate"})
    ui.site.write(data)
    page = ui.open()
    expect(page.locator(HL_ITEMS)).to_have_count(N)
    expect(page.locator("#dots .dot")).to_have_count(N)
    expect(page.locator("#main")).not_to_contain_text("Duplicate")


def test_huge_strings_are_capped_and_fast(ui):
    blob = "a" * 200_000
    ui.site.write(
        make_reports(
            topics={0: {"title": "T" * 5000}},
            reports={
                0: {
                    "headline": "word " * 40_000,
                    # no sentence end until the very end: the old regex was quadratic on this
                    "summary": blob + ".b",
                    "items": [
                        {"title": "x" * 10_000, "url": "https://example.com/" + "p" * 5000},
                        {"title": "ok", "url": "https://example.com/ok", "source": "S" * 9000},
                    ],
                }
            },
        )
    )
    page = ui.open()
    expect(page.locator(HL_ITEMS)).to_have_count(N)
    assert len(list_item(page, 0).locator(".hl-topic").inner_text()) <= 80
    assert len(list_item(page, 0).locator(".hl-text").get_attribute("data-full")) <= 2000
    assert len(list_item(page, 0).get_attribute("aria-label")) < 2200
    list_item(page, 0).click()
    slide = page.locator("#storyStage .slide").last
    assert len(slide.locator(".slide-sum").inner_text()) <= 600
    chips = slide.locator("a.chip")
    expect(chips).to_have_count(1)  # the 5 kB URL is dropped
    assert len(chips.first.locator(".chip-src").inner_text()) <= 60


def test_summary_keeps_decimals_and_domains_intact(ui):
    ui.site.write(
        make_reports(
            reports={
                0: {"summary": "Version 2.5 of example.com is out. It is 3.2x faster! Third."},
                1: {"summary": 'He said "Done." Then left. Not shown.'},
            }
        )
    )
    page = ui.open()
    list_item(page, 0).click()
    expect(page.locator("#storyStage .slide").last.locator(".slide-sum")).to_have_text(
        "Version 2.5 of example.com is out. It is 3.2x faster!"
    )
    page.keyboard.press("ArrowRight")
    expect(page.locator("#storyStage .slide").last.locator(".slide-sum")).to_have_text(
        'He said "Done." Then left.'
    )


def test_aria_labels_do_not_double_the_full_stop(ui):
    ui.site.write(make_reports(reports={1: None, 2: {"headline": "No stop here"}}))
    page = ui.open()
    title0, title1, title2 = TOPICS[0][1], TOPICS[1][1], TOPICS[2][1]
    expect(list_item(page, 0)).to_have_attribute(
        "aria-label", f"{title0}: {headline(0)} Open story"
    )
    expect(list_item(page, 1)).to_have_attribute("aria-label", f"{title1}: Coming soon. Open story")
    expect(list_item(page, 2)).to_have_attribute(
        "aria-label", f"{title2}: No stop here. Open story"
    )
    expect(page.locator("#deck .card[data-i='0']")).to_have_attribute(
        "aria-label", f"{title0}: {headline(0)} Open story"
    )


@pytest.mark.parametrize(
    "seen",
    ['{"day":"2026-11-23","ids":["ai_news"]}', '{"day":"2026-11-23","ids":{"ai_news":5}}'],
    ids=["ids-array", "ids-non-string"],
)
def test_malformed_seen_storage_is_ignored(ui, seen):
    script = f"localStorage.setItem('mybuddy.seen.v2', {json.dumps(seen)});"
    page = ui.open(init_scripts=(script,))
    expect(page.locator("#bubbles li.topic .bubble.seen")).to_have_count(0)
    list_item(page, 0).click()
    assert story_index(page) == 0
    page.keyboard.press("Escape")
    expect(page.locator("#bubbles li.topic .bubble.seen")).to_have_count(1)


# ---------------------------------------------------------------- stalled loads, retry


def test_stalled_request_times_out_to_retry(ui):
    stalled = []
    ctx = ui.new_context()
    ctx.route("**/data/reports.json", lambda route: stalled.append(route))  # never answered
    page = ui.open(context=ctx, clock=True, wait=False)
    page.wait_for_function("() => document.readyState === 'complete'")
    expect(page.locator("#loadErr")).to_be_hidden()
    page.clock.run_for(16_000)
    expect(page.locator("#loadErr")).to_be_visible()
    expect(page.locator("#deck .shimmer")).to_have_count(0)
    ctx.unroute("**/data/reports.json")
    page.get_by_role("button", name="Retry").click()
    expect(page.locator(HL_ITEMS)).to_have_count(N)
    expect(page.locator("#loadErr")).to_be_hidden()


def test_double_retry_builds_one_deck(ui):
    ui.site.write("{ not json")
    page = ui.open()
    expect(page.locator("#loadErr")).to_be_visible()
    ui.site.write(make_reports())
    page.evaluate("() => { const b = document.querySelector('#retryBtn'); b.click(); b.click(); }")
    expect(page.locator(HL_ITEMS)).to_have_count(N)
    expect(page.locator("#dots .dot")).to_have_count(N)
    expect(page.locator("#deck .card:not(.skeleton)")).to_have_count(N)


# ---------------------------------------------------------------- missing browser APIs

NO_APIS = """
  delete window.IntersectionObserver;
  delete Document.prototype.startViewTransition;
  window.matchMedia = undefined;
  delete window.AbortController;
"""


def test_works_without_optional_browser_apis(ui):
    page = ui.open(init_scripts=(NO_APIS,))
    expect(page.locator(HL_ITEMS)).to_have_count(N)
    expect(page.locator("#flow")).to_have_class(re.compile(r"\bin\b"))  # revealed without IO
    page.locator("#themeBtn").click()  # no view transition: switches at once
    expect(page.locator("html")).to_have_attribute("data-mode", "light")
    _exercise(page)


# ---------------------------------------------------------------- app link fallback

BLOCK_POPUPS = """() => { window.__app = [];
  window.addEventListener('mybuddy:open-app', (e) => { window.__app.push(e.detail.url); e.preventDefault(); });
  window.open = () => null; }"""


def test_blocked_popup_falls_back_to_this_tab(ui):
    page = ui.open()
    page.locator("#subscribe").scroll_into_view_if_needed()
    page.locator("#subsChips .tchip:not(.tc-all)").first.click()
    href = page.locator("#subsBtn").get_attribute("href")
    page.evaluate(BLOCK_POPUPS)
    page.locator("#subsBtn").click()
    page.wait_for_url(href, timeout=5000)
    assert len(page.context.pages) == 1


# ---------------------------------------------------------------- Content-Security-Policy


def _csp(html: str) -> str:
    m = re.search(r'<meta http-equiv="Content-Security-Policy" content="([^"]+)">', html)
    assert m, "no CSP meta tag"
    return m.group(1)


def _sha(text: str) -> str:
    return "'sha256-" + base64.b64encode(hashlib.sha256(text.encode()).digest()).decode() + "'"


@pytest.mark.parametrize("name", ["index.html", "404.html"])
def test_csp_allows_exactly_the_inline_code(name):
    html = re.sub(r"<!--.*?-->", "", (SITE_DIR / name).read_text(encoding="utf-8"), flags=re.S)
    csp = _csp(html)
    directives = dict(d.strip().split(" ", 1) for d in csp.split(";") if d.strip())
    for body in re.findall(r"<script>(.*?)</script>", html, re.S):
        assert _sha(body) in directives["script-src"], "inline script changed: update its hash"
    for body in re.findall(r"<style>(.*?)</style>", html, re.S):
        assert _sha(body) in directives["style-src"], "inline style changed: update its hash"
    assert "'unsafe-inline'" not in csp and "'unsafe-eval'" not in csp
    assert not re.search(r"\sstyle=", html), "style attributes are blocked by the CSP"
    assert not re.search(r"\son[a-z]+=", html), "inline event handlers are blocked by the CSP"
    assert '<meta name="referrer" content="strict-origin-when-cross-origin">' in html
    scripts = re.findall(r"<script\b[^>]*\bsrc=\"([^\"]+)\"", html)
    assert all(not s.startswith(("http:", "https:", "//")) for s in scripts), scripts


VIOLATIONS = """window.__csp = [];
  document.addEventListener('securitypolicyviolation',
    (e) => window.__csp.push(e.violatedDirective + ' ' + e.blockedURI));"""


def test_page_runs_under_the_csp_without_violations(ui):
    page = ui.open(init_scripts=(VIOLATIONS,))
    expect(page.locator(HL_ITEMS)).to_have_count(N)
    # the pre-paint script ran (it is allowed by hash) and stylesheet-driven stagger survived
    expect(page.locator("html")).to_have_attribute("data-palette", "periwinkle")
    delay = (
        page.locator(".hero-title .w")
        .nth(3)
        .locator("> span")
        .evaluate("n => getComputedStyle(n).animationDelay")
    )
    assert delay == "0.435s", delay  # 3 * 95ms + .15s: --i comes from styles.css, not style=""
    _exercise(page)
    page.locator("#themeBtn").click()
    assert page.evaluate("window.__csp") == []


def test_404_runs_under_its_csp(ui):
    ui.allow_console(r"404")
    page = ui.open("nope/missing", wait=False)
    expect(page.get_by_role("heading", name="Page not found")).to_be_visible()
    expect(page.locator("html")).to_have_attribute("data-theme", "dark")
    # the hashed <style> applied: the card is styled
    assert page.locator(".home").evaluate("n => getComputedStyle(n).borderRadius") == "999px"


# ---------------------------------------------------------------- copy rules

FORBIDDEN = re.compile(r"\bIST\b|telegram|free ai", re.I)


def _visible_copy(html: str) -> str:
    html = re.sub(r"<script\b.*?</script>|<style\b.*?</style>|<!--.*?-->", " ", html, flags=re.S)
    attrs = re.findall(r'(?:aria-label|title|alt|content)="([^"]*)"', html)
    return re.sub(r"<[^>]+>", " ", html) + "\n" + "\n".join(attrs)


@pytest.mark.parametrize("name", ["index.html", "404.html"])
def test_static_copy_rules(name):
    text = _visible_copy((SITE_DIR / name).read_text(encoding="utf-8"))
    bad = FORBIDDEN.findall(text)
    assert not bad, bad
    assert not re.search(r"\bsummariz", text, re.I), "use British spelling (summarise)"


def test_name_never_appears_in_site_or_tests():
    root = SITE_DIR.parent
    pat = re.compile("a" + "man", re.I)  # split so this file does not match itself
    hits = [
        str(f)
        for base in (SITE_DIR, root / "tests" / "ui")
        for f in base.rglob("*")
        if f.is_file()
        and f.suffix in {".html", ".js", ".css", ".json", ".py", ".md", ".txt"}
        and pat.search(f.read_text(encoding="utf-8"))
    ]
    assert not hits, hits
