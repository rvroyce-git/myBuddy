"""The "Get daily updates" panel: topic chips from the data, the bot start link, the QR code,
remembered picks, the navbar bell, motion, layout and contrast."""

from __future__ import annotations

import json
import re

import pytest

pytest.importorskip("playwright")

from playwright.sync_api import expect  # noqa: E402
from ui_support import TOPICS, N, make_reports  # noqa: E402

pytestmark = pytest.mark.ui

BOT = "https://t.me/myBuddy_update_bot"
KEY = "mybuddy.subs.v1"
CHIPS = "#subsChips .tchip:not(.tc-all)"
ALL = "#subsChips .tc-all"
BTN = "#subsBtn"
PAYLOAD = re.compile(r"^t[0-9a-f]{1,15}$")
DESKTOP = {"width": 1280, "height": 900}
MOBILE = {"width": 390, "height": 844}


def _panel(page, settle=True):
    """Scroll the panel in; by default also wait for the chips' staggered entrance to end."""
    page.locator("#subscribe").scroll_into_view_if_needed()
    expect(page.locator("#subscribe")).to_have_class(re.compile(r"\bin\b"))
    expect(page.locator("#subsChips .tchip.skel")).to_have_count(0)
    if settle:
        expect(page.locator("#subscribe")).to_have_class(re.compile(r"\bdone\b"))
    return page


def _pressed(page):
    return page.evaluate(
        f"() => [...document.querySelectorAll('{CHIPS}')]"
        ".filter(c => c.getAttribute('aria-pressed') === 'true').map(c => c.dataset.id)"
    )


def _payload(page):
    href = page.locator(BTN).get_attribute("href")
    assert href and href.startswith(BOT + "?start="), href
    p = href.split("?start=", 1)[1]
    assert PAYLOAD.match(p), p
    return p


def _mask(indices):
    return "t" + format(sum(1 << i for i in indices), "x")


def _expect_disabled(page):
    btn = page.locator(BTN)
    expect(btn).not_to_have_attribute("href", re.compile(".*"))
    expect(btn).to_have_attribute("aria-disabled", "true")


# ---------------------------------------------------------------- chips follow the data


def test_chips_match_reports_topics_in_order(ui):
    page = _panel(ui.open())
    chips = page.locator(CHIPS)
    expect(chips).to_have_count(N)
    assert page.evaluate(
        f"() => [...document.querySelectorAll('{CHIPS}')].map(c => c.dataset.id)"
    ) == [t[0] for t in TOPICS]
    expect(chips).to_have_text([t[2] for t in TOPICS])  # same short labels as the bubbles
    expect(page.locator(ALL)).to_have_text("All")
    # same icons as the headline list
    icons = page.evaluate(
        f"""() => [...document.querySelectorAll('{CHIPS}')].map((c, k) =>
            c.querySelector('.tc-ico svg.ico').innerHTML ===
            document.querySelectorAll('#hlList .hl-ico svg.ico')[k].innerHTML)"""
    )
    assert icons == [True] * N
    for c in page.locator("#subsChips .tchip").all():
        expect(c).to_have_attribute("aria-pressed", "false")
        assert c.evaluate("n => n.tagName") == "BUTTON"
        box = c.bounding_box()
        assert box["height"] >= 44 and box["width"] >= 44
    _expect_disabled(page)


def test_chips_follow_changed_topics(ui):
    """Chips follow the data; each link bit is the topic's own stable `bit`, not its position."""
    doc = make_reports(topics={2: None})  # GitHub (bit 2) removed
    extra = {**doc["topics"][0], "id": "robotics", "title": "Robots", "emoji": "🤖", "bit": 7}
    doc["topics"].append(extra)
    ui.site.write(doc)
    page = _panel(ui.open())
    ids = [t[0] for k, t in enumerate(TOPICS) if k != 2] + ["robotics"]
    assert _chip_ids(page) == ids
    expect(page.locator(CHIPS).last.locator(".tc-txt")).to_have_text("Robots")
    expect(page.locator(CHIPS).last.locator(".emoji")).to_have_text("🤖")
    page.locator(CHIPS).last.click()
    assert _payload(page) == "t80"  # bit 7
    page.locator(CHIPS).nth(2).click()  # deep_dive keeps bit 3 although it moved up
    assert _payload(page) == "t88"
    page.locator(ALL).click()
    assert _payload(page) == "tfb"  # 0..7 without 2
    doc["topics"].append({**extra, "id": "space", "title": "Space", "bit": 59})
    ui.site.write(doc)
    page.reload()
    _panel(page)
    expect(page.locator(CHIPS)).to_have_count(8)
    page.locator(ALL).click()
    assert _payload(page) == "t8000000000000fb"  # bit 59: the longest allowed payload


# ---------------------------------------------------------------- stable topic bits (v2)

MISSING = object()
DEFAULT_BITS = {t[0]: k for k, t in enumerate(TOPICS)}


def _chip_ids(page):
    return page.evaluate(f"() => [...document.querySelectorAll('{CHIPS}')].map(c => c.dataset.id)")


def _doc(spec):
    """reports.json from [(id, bit)]; ``MISSING`` leaves the bit out (older data)."""
    base = {t["id"]: t for t in make_reports()["topics"]}
    topics = []
    for tid, bit in spec:
        t = dict(base.get(tid) or {**base["ai_news"], "id": tid, "title": tid.title()})
        t.pop("bit", None)
        if bit is not MISSING:
            t["bit"] = bit
        topics.append(t)
    return {"generated_at": "2026-11-23T08:15:02+05:30", "topics": topics}


def _open_with(ui, doc, **kw):
    ctx = ui.new_context(**kw)
    body = json.dumps(doc)
    ctx.route(
        "**/data/reports.json",
        lambda route: route.fulfill(status=200, body=body, content_type="application/json"),
    )
    return _panel(ui.open(context=ctx))


IDS = [t[0] for t in TOPICS]
BIT_CASES = {
    "reversed-with-bits": [(i, DEFAULT_BITS[i]) for i in reversed(IDS)],
    "reversed-no-bits": [(i, MISSING) for i in reversed(IDS)],
    "security-removed": [(i, DEFAULT_BITS[i]) for i in IDS if i != "security"],
    "new-topic-bit-9": [(i, DEFAULT_BITS[i]) for i in IDS] + [("robots", 9)],
    "new-topic-without-bit": [(i, MISSING) for i in IDS] + [("robots", MISSING)],
    "mixed-missing": [("startups", MISSING), ("ai_news", 0), ("robots", 12), ("security", None)],
    "moved-bit": [("ai_news", 10), ("security", MISSING), ("deep_dive", 3)],
    "invalid-bits": [
        ("ai_news", "0"),
        ("security", 1.5),
        ("github_trending", -1),
        ("deep_dive", 60),
        ("research_papers", True),
        ("startups", 5),
        ("ai_models", MISSING),
    ],
    "duplicate-bits": [("ai_news", 0), ("security", 2), ("github_trending", 2), ("startups", 5)],
    "duplicate-with-fallback": [("ai_news", MISSING), ("robots", 0), ("security", 1)],
}


def _expected(spec):
    """[(id, bit)] of the chips that should show, mirroring the contract."""
    raw = []
    for tid, bit in spec:
        b = DEFAULT_BITS.get(tid) if bit is MISSING or bit is None else bit
        ok = isinstance(b, int) and not isinstance(b, bool) and 0 <= b <= 59
        raw.append((tid, b if ok else None))
    seen = [b for _, b in raw if b is not None]
    return [(tid, b) for tid, b in raw if b is not None and seen.count(b) == 1]


@pytest.mark.parametrize("case", list(BIT_CASES))
def test_payload_uses_stable_topic_bits(ui, case):
    spec = BIT_CASES[case]
    want = _expected(spec)
    assert want, case
    page = _open_with(ui, _doc(spec))
    assert _chip_ids(page) == [tid for tid, _ in want]  # bad or shared bits: no chip
    for k, (tid, bit) in enumerate(want):
        chip = page.locator(CHIPS).nth(k)
        chip.click()
        assert _payload(page) == "t" + format(1 << bit, "x"), (tid, bit)
        assert _qr(page) == page.locator(BTN).get_attribute("href")
        chip.click()
    page.locator(ALL).click()
    assert _payload(page) == "t" + format(sum(1 << b for _, b in want), "x")


def test_expected_cases_cover_the_rules():
    assert _expected(BIT_CASES["invalid-bits"]) == [("startups", 5), ("ai_models", 6)]
    assert _expected(BIT_CASES["duplicate-bits"]) == [("ai_news", 0), ("startups", 5)]
    assert _expected(BIT_CASES["duplicate-with-fallback"]) == [("security", 1)]
    assert _expected(BIT_CASES["mixed-missing"]) == [
        ("startups", 5),
        ("ai_news", 0),
        ("robots", 12),
        ("security", 1),
    ]
    assert _expected(BIT_CASES["new-topic-without-bit"]) == [(i, k) for k, i in enumerate(IDS)]


def test_saved_picks_are_kept_by_id_when_bits_move(ui):
    spec = [("security", 1), ("ai_news", 10)]
    script = f"localStorage.setItem('{KEY}', JSON.stringify(['ai_news']))"
    page = _open_with(ui, _doc(spec), init_scripts=(script,))
    assert _pressed(page) == ["ai_news"]
    assert _payload(page) == "t400"


def test_fallback_map_is_one_constant_matching_config():
    import yaml  # noqa: PLC0415
    from conftest import SITE_DIR  # noqa: PLC0415

    js = (SITE_DIR / "assets" / "app.js").read_text(encoding="utf-8")
    m = re.search(r"const TOPIC_BITS = \{([^}]*)\}", js)
    assert m and js.count("TOPIC_BITS = ") == 1
    pairs = dict(re.findall(r"(\w+): (\d+)", m.group(1)))
    assert {k: int(v) for k, v in pairs.items()} == DEFAULT_BITS
    cfg = yaml.safe_load((SITE_DIR.parent / "config" / "topics.yaml").read_text(encoding="utf-8"))
    ai = [t for t in cfg["topics"] if t.get("ai")]
    if all("bit" in t for t in ai):  # once the server side has added stable bits
        assert {t["id"]: t["bit"] for t in ai} == DEFAULT_BITS


def test_security_brings_urgent_alerts_note(ui):
    page = _panel(ui.open())
    note = page.locator("#subsNote")
    expect(note).to_be_hidden()
    sec = page.locator(f"{CHIPS}[data-id='security']")
    expect(sec).to_have_attribute("title", "+ urgent alerts")
    expect(sec.locator(".tc-bolt")).to_have_count(1)
    before = page.locator("#subsCard").bounding_box()["height"]
    page.locator(f"{CHIPS}[data-id='ai_news']").click()
    expect(note).to_be_hidden()
    sec.click()
    expect(note).to_be_visible()
    expect(note).to_have_text("Security adds urgent alerts too.")
    assert page.locator("#subsCard").bounding_box()["height"] == before  # no layout shift
    sec.click()
    expect(note).to_be_hidden()


# ---------------------------------------------------------------- start link


@pytest.mark.parametrize(
    "picks",
    [[0], [1], [6], [0, 1], [2, 4], [1, 3, 5], list(range(N))],
    ids=lambda p: "+".join(map(str, p)),
)
def test_payload_for_selection(ui, picks):
    page = _panel(ui.open())
    _expect_disabled(page)
    for k in picks:
        page.locator(CHIPS).nth(k).click()
    assert _payload(page) == _mask(picks)
    expect(page.locator("#subsN")).to_have_text(str(len(picks)))
    all_on = "true" if len(picks) == N else "false"
    expect(page.locator(ALL)).to_have_attribute("aria-pressed", all_on)


def test_contract_examples_and_all_toggle(ui):
    page = _panel(ui.open())
    page.get_by_role("button", name="AI news", exact=True).click()
    page.get_by_role("button", name="Security", exact=True).click()
    assert _payload(page) == "t3"
    page.locator(ALL).click()
    assert _payload(page) == "t7f"
    assert _pressed(page) == [t[0] for t in TOPICS]
    page.locator(ALL).click()  # all on -> all off
    assert _pressed(page) == []
    _expect_disabled(page)
    page.locator(CHIPS).nth(3).click()
    page.locator(CHIPS).nth(3).click()
    _expect_disabled(page)


def test_link_attributes(ui):
    page = _panel(ui.open())
    page.locator(CHIPS).nth(0).click()
    btn = page.locator(BTN)
    expect(btn).to_have_attribute("target", "_blank")
    rel = btn.get_attribute("rel").split()
    assert "noopener" in rel and "noreferrer" in rel
    assert btn.get_attribute("href").startswith("https://")
    expect(btn).to_have_text(re.compile(r"Get daily updates"))
    expect(btn).not_to_have_attribute("aria-disabled", re.compile(".*"))
    expect(btn).to_have_accessible_name(re.compile(r"^Get daily updates .*new tab"))


def test_disabled_button_does_not_navigate(ui):
    page = _panel(ui.open())
    url = page.url
    page.locator(BTN).click(force=True)
    page.locator(BTN).focus()
    page.keyboard.press("Enter")
    page.wait_for_timeout(300)
    assert page.url == url and len(page.context.pages) == 1


# ---------------------------------------------------------------- app link first, web fallback

APP = "tg://resolve?domain=myBuddy_update_bot&start="
# records each app-link attempt and cancels the real navigation (no app in the test browser)
WATCH_APP = """() => { window.__app = [];
  window.addEventListener('mybuddy:open-app', (e) => { window.__app.push(e.detail.url); e.preventDefault(); });
  window.__prevented = null;
  window.addEventListener('click', (e) => { window.__prevented = e.defaultPrevented; }); }"""


def _ready(ui, picks=(0, 1)):
    page = _panel(ui.open())
    for k in picks:
        page.locator(CHIPS).nth(k).click()
    page.evaluate(WATCH_APP)
    return page


def test_click_tries_the_app_link_first(ui):
    page = _ready(ui)
    p = _payload(page)
    assert p == _mask([0, 1])
    page.locator(BTN).click()
    assert page.evaluate("window.__app") == [APP + p]
    assert page.evaluate("window.__prevented") is True
    assert page.locator(BTN).get_attribute("href") == f"{BOT}?start={p}"  # href stays the web link


def test_falls_back_to_web_link_when_app_does_not_open(ui):
    page = _ready(ui, picks=(2,))
    p = _payload(page)
    with page.context.expect_page(timeout=5000) as info:
        page.locator(BTN).click()
    new = info.value
    assert new.url == f"{BOT}?start={p}"
    assert page.evaluate("window.__app") == [APP + p]
    assert new.evaluate("window.opener") is None  # noopener
    assert len(page.context.pages) == 2


def test_no_fallback_when_app_took_over(ui):
    page = _ready(ui)
    page.locator(BTN).click()
    page.evaluate(
        """() => { Object.defineProperty(document, 'hidden', { configurable: true, get: () => true });
                   Object.defineProperty(document, 'visibilityState', { configurable: true, get: () => 'hidden' });
                   document.dispatchEvent(new Event('visibilitychange')); }"""
    )
    page.evaluate(
        """() => { delete document.hidden; delete document.visibilityState;
                   document.dispatchEvent(new Event('visibilitychange')); }"""
    )
    page.wait_for_timeout(1800)
    assert len(page.evaluate("window.__app")) == 1
    assert len(page.context.pages) == 1


def test_modifier_click_is_not_intercepted(ui):
    page = _ready(ui)
    p = _payload(page)
    with page.context.expect_page(timeout=5000) as info:
        page.locator(BTN).click(modifiers=["ControlOrMeta"])
    assert info.value.url == f"{BOT}?start={p}"
    assert page.evaluate("window.__app") == []
    assert page.evaluate("window.__prevented") is False


def test_disabled_button_does_not_try_the_app(ui):
    page = _panel(ui.open())
    page.evaluate(WATCH_APP)
    page.locator(BTN).click(force=True)
    page.wait_for_timeout(1500)
    assert page.evaluate("window.__app") == []
    assert len(page.context.pages) == 1


def test_open_in_browser_link(ui):
    page = _panel(ui.open())
    web = page.locator("#subsWeb")
    expect(web).to_be_hidden()
    page.locator(CHIPS).nth(0).click()
    expect(web).to_be_visible()
    expect(web).to_have_text("Open in browser")
    assert web.get_attribute("href") == page.locator(BTN).get_attribute("href")
    expect(web).to_have_attribute("target", "_blank")
    rel = web.get_attribute("rel").split()
    assert "noopener" in rel and "noreferrer" in rel
    page.evaluate(WATCH_APP)
    with page.context.expect_page(timeout=5000) as info:
        web.click()
    assert info.value.url == page.locator(BTN).get_attribute("href")
    assert page.evaluate("window.__app") == []


def test_bot_username_lives_in_one_constant():
    from conftest import SITE_DIR  # noqa: PLC0415

    js = (SITE_DIR / "assets" / "app.js").read_text(encoding="utf-8")
    assert js.count("myBuddy_update_bot") == 1
    assert "myBuddy_update_bot" not in (SITE_DIR / "index.html").read_text(encoding="utf-8")


# ---------------------------------------------------------------- remembered picks


def test_selection_is_remembered_and_unknown_ids_are_dropped(ui):
    seed = json.dumps(["security", "ghost", 5, "ai_news"])
    script = f"if (!sessionStorage.seeded) {{ localStorage.setItem('{KEY}', '{seed}'); sessionStorage.seeded = 1; }}"
    page = _panel(ui.open(init_scripts=(script,)))
    assert _pressed(page) == ["ai_news", "security"]
    assert _payload(page) == "t3"
    page.locator(CHIPS).nth(2).click()
    stored = json.loads(page.evaluate(f"() => localStorage.getItem('{KEY}')"))
    assert stored == ["ai_news", "security", "github_trending"]
    page.reload()
    _panel(page)
    assert _pressed(page) == ["ai_news", "security", "github_trending"]
    assert _payload(page) == "t7"


@pytest.mark.parametrize("bad", ["not json", '{"a":1}', "null", "[1,2]"])
def test_corrupt_saved_selection_is_ignored(ui, bad):
    page = _panel(ui.open(init_scripts=(f"localStorage.setItem('{KEY}', {json.dumps(bad)})",)))
    assert _pressed(page) == []
    _expect_disabled(page)


def test_works_when_storage_throws(ui):
    script = """Object.defineProperty(window, 'localStorage', { get() { throw new Error('blocked'); } });"""
    page = _panel(ui.open(init_scripts=(script,)))
    page.locator(CHIPS).nth(4).click()
    assert _payload(page) == "t10"


# ---------------------------------------------------------------- keyboard


def test_keyboard_toggles_chips(ui):
    page = _panel(ui.open())
    chip = page.locator(CHIPS).nth(1)
    chip.focus()
    page.keyboard.press("Space")
    expect(chip).to_have_attribute("aria-pressed", "true")
    page.keyboard.press("Enter")
    expect(chip).to_have_attribute("aria-pressed", "false")
    page.keyboard.press("Enter")
    page.keyboard.press("Tab")  # to the next chip
    page.keyboard.press("Space")
    assert _payload(page) == "t6"
    page.locator(ALL).focus()
    page.keyboard.press("Space")
    assert _payload(page) == "t7f"
    # the button is reachable by keyboard
    page.locator(CHIPS).last.focus()
    page.keyboard.press("Tab")
    expect(page.locator(BTN)).to_be_focused()


# ---------------------------------------------------------------- navbar bell


@pytest.mark.parametrize("viewport", [DESKTOP, MOBILE], ids=["desktop", "mobile"])
def test_bell_scrolls_to_and_focuses_the_panel(ui, viewport):
    page = ui.open(viewport=viewport)
    bell = page.get_by_role("link", name="Get updates")
    expect(bell).to_be_visible()
    box = bell.bounding_box()
    assert box["width"] >= 44 and box["height"] >= 44
    assert page.evaluate("scrollY") == 0
    bell.click()
    expect(page.locator("#subscribe")).to_be_focused()
    expect(page.locator("#subsCard")).to_have_class(re.compile(r"\bflash\b"))
    page.wait_for_function(
        """() => { const r = document.querySelector('#subscribe').getBoundingClientRect();
                   return r.top > 40 && r.top < 120; }""",
        polling=100,
    )
    assert page.evaluate("scrollY") > 200
    expect(page.locator("#subsCard")).to_have_css("opacity", "1")


def test_bell_text_shows_on_wide_screens_only(ui):
    page = ui.open(viewport=MOBILE)
    assert page.locator("#bellBtn .bell-txt").bounding_box()["width"] <= 1
    page.set_viewport_size(DESKTOP)
    assert page.locator("#bellBtn .bell-txt").bounding_box()["width"] > 40


# ---------------------------------------------------------------- motion


def test_chips_stagger_in_and_pop(ui):
    page = ui.open(viewport=MOBILE)  # the panel starts below the fold
    expect(page.locator("#subscribe")).not_to_have_class(re.compile(r"\bin\b"))
    assert page.locator("#subsCard").evaluate("n => getComputedStyle(n).opacity") == "0"
    _panel(page, settle=False)
    delays = page.evaluate(
        f"() => [...document.querySelectorAll('{CHIPS}')].map(c => getComputedStyle(c).animationDelay)"
    )
    assert len(set(delays)) == N  # each chip has its own delay
    chip = page.locator(CHIPS).nth(0)
    expect(page.locator("#subscribe")).to_have_class(re.compile(r"\bdone\b"))
    chip.click()
    expect(chip).to_have_class(re.compile(r"\bpop\b"))
    expect(chip).not_to_have_class(re.compile(r"\bpop\b"))  # cleaned up after the pop
    glow = page.locator(BTN).evaluate("n => getComputedStyle(n).animationName")
    assert "btnGlow" in glow


def test_reduced_motion(ui):
    page = ui.open(reduced_motion="reduce")
    expect(page.locator("#subscribe")).to_have_class(re.compile(r"\bin\b"))
    hidden = page.evaluate(
        """() => [...document.querySelectorAll('#subsCard, #subsChips .tchip')]
              .filter(n => parseFloat(getComputedStyle(n).opacity) < 0.99).length"""
    )
    assert hidden == 0
    page.locator("#subscribe").scroll_into_view_if_needed()
    page.locator(CHIPS).nth(0).click()
    expect(page.locator(CHIPS).nth(0)).not_to_have_class(re.compile(r"\bpop\b"))
    running = page.evaluate(
        """() => document.getAnimations().filter(a => {
              const t = a.effect && a.effect.target;
              return t && (t.closest('#subscribe') || t.closest('#bellBtn'))
                && a.effect.getComputedTiming().duration > 1;
           }).length"""
    )
    assert running == 0
    page.locator("#bellBtn").click()
    expect(page.locator("#subscribe")).to_be_focused()


# ---------------------------------------------------------------- layout


@pytest.mark.parametrize("width", [360, 390, 1024, 1440])
def test_no_horizontal_scroll(ui, width):
    page = _panel(ui.open(viewport={"width": width, "height": 800}))
    page.locator(ALL).click()
    page.wait_for_timeout(700)
    assert page.evaluate("document.documentElement.scrollWidth") <= width
    box = page.locator("#subsCard").bounding_box()
    assert box["x"] >= 15 and box["x"] + box["width"] <= width - 15
    for sel in (CHIPS, BTN, ".subs-bear"):
        for el in page.locator(sel).all():
            b = el.bounding_box()
            assert b["x"] >= 0 and b["x"] + b["width"] <= width, sel


def test_panel_sits_between_headlines_and_how_it_works(ui):
    page = ui.open()
    order = page.evaluate(
        "() => ['.headlines', '#subscribe', '.how', '#flow'].map(s => document.querySelector(s)"
        ".getBoundingClientRect().top + scrollY)"
    )
    assert order == sorted(order)


def test_choosing_topics_does_not_shift_layout(ui):
    page = _panel(ui.open(viewport=MOBILE))
    before = page.locator("#subsCard").bounding_box()
    btn = page.locator(BTN).bounding_box()
    page.locator(CHIPS).nth(0).click()
    page.wait_for_timeout(700)
    assert page.locator("#subsCard").bounding_box()["height"] == before["height"]
    after = page.locator(BTN).bounding_box()
    assert abs(after["width"] - btn["width"]) < 1 and abs(after["y"] - btn["y"]) < 1


# ---------------------------------------------------------------- QR code

GF_EXP = [0] * 512
GF_LOG = [0] * 256
_x = 1
for _i in range(255):
    GF_EXP[_i] = _x
    GF_LOG[_x] = _i
    _x <<= 1
    if _x & 0x100:
        _x ^= 0x11D
for _i in range(255, 512):
    GF_EXP[_i] = GF_EXP[_i - 255]

# version -> (total codewords, ECC per block, blocks) at level M (ISO/IEC 18004 table 9)
QR_M = {
    1: (26, 10, 1),
    2: (44, 16, 1),
    3: (70, 26, 1),
    4: (100, 18, 2),
    5: (134, 24, 2),
    6: (172, 16, 4),
}


def _format_codes():
    out = {}
    for data in range(32):
        rem = data
        for _ in range(10):
            rem = (rem << 1) ^ ((rem >> 9) * 0x537)
        out[((data << 10) | rem) ^ 0x5412] = data
    return out


def _mask_hit(m, x, y):
    return [
        (x + y) % 2 == 0,
        y % 2 == 0,
        x % 3 == 0,
        (x + y) % 3 == 0,
        (x // 3 + y // 2) % 2 == 0,
        (x * y) % 2 + (x * y) % 3 == 0,
        ((x * y) % 2 + (x * y) % 3) % 2 == 0,
        ((x + y) % 2 + (x * y) % 3) % 2 == 0,
    ][m]


def _decode_qr(n, path):
    """Decode a level-M byte-mode QR from the page's SVG path; checks every RS block."""
    q = 4
    grid = [[0] * n for _ in range(n)]
    for x, y in re.findall(r"M(\d+) (\d+)h1v1h-1z", path):
        grid[int(y) - q][int(x) - q] = 1
    v = (n - 17) // 4
    assert v in QR_M
    # finder patterns
    for cx, cy in ((0, 0), (n - 7, 0), (0, n - 7)):
        for dy in range(7):
            for dx in range(7):
                d = max(abs(dx - 3), abs(dy - 3))
                assert grid[cy + dy][cx + dx] == (0 if d == 2 else 1)
    assert all(grid[6][i] == (i + 1) % 2 for i in range(8, n - 8))  # timing
    codes = _format_codes()
    bits1 = [grid[i][8] for i in range(6)] + [grid[7][8], grid[8][8], grid[8][7]]
    bits1 += [grid[8][14 - i] for i in range(9, 15)]
    bits2 = [grid[8][n - 1 - i] for i in range(8)] + [grid[n - 15 + i][8] for i in range(8, 15)]
    f1 = sum(b << i for i, b in enumerate(bits1))
    f2 = sum(b << i for i, b in enumerate(bits2))
    assert f1 == f2 and f1 in codes
    ecl, mask = codes[f1] >> 3, codes[f1] & 7
    assert ecl == 0  # level M
    fn = [[False] * n for _ in range(n)]
    for i in range(n):
        fn[6][i] = fn[i][6] = True
    for cx, cy in ((3, 3), (n - 4, 3), (3, n - 4)):
        for dy in range(-4, 5):
            for dx in range(-4, 5):
                if 0 <= cx + dx < n and 0 <= cy + dy < n:
                    fn[cy + dy][cx + dx] = True
    if v >= 2:
        for dy in range(-2, 3):
            for dx in range(-2, 3):
                fn[n - 7 + dy][n - 7 + dx] = True
    for i in range(9):
        fn[8][i] = fn[i][8] = True
    for i in range(8):
        fn[8][n - 1 - i] = fn[n - 1 - i][8] = True
    bits = []
    right = n - 1
    while right >= 1:
        if right == 6:
            right = 5
        for vert in range(n):
            for j in range(2):
                x = right - j
                y = n - 1 - vert if ((right + 1) & 2) == 0 else vert
                if not fn[y][x]:
                    bits.append(grid[y][x] ^ int(_mask_hit(mask, x, y)))
        right -= 2
    words = [int("".join(map(str, bits[i : i + 8])), 2) for i in range(0, len(bits) // 8 * 8, 8)]
    total, ecc, nb = QR_M[v]
    words = words[:total]
    dlen = total // nb - ecc
    blocks = [[words[i * nb + b] for i in range(dlen)] for b in range(nb)]
    for b in range(nb):
        blocks[b] += [words[dlen * nb + i * nb + b] for i in range(ecc)]
        cw = blocks[b]
        for k in range(ecc):  # every syndrome of a valid RS codeword is zero
            s = 0
            for c in cw:
                s = (GF_EXP[GF_LOG[s] + k] if s else 0) ^ c
            assert s == 0, f"block {b} syndrome {k}"
    data = [c for blk in blocks for c in blk[:dlen]]
    stream = "".join(format(c, "08b") for c in data)
    assert stream[:4] == "0100"  # byte mode
    count = int(stream[4:12], 2)
    return bytes(int(stream[12 + 8 * i : 20 + 8 * i], 2) for i in range(count)).decode()


def _qr(page):
    svg = page.locator("#qrSvg")
    return _decode_qr(int(svg.get_attribute("data-n")), svg.locator(".qr-dots").get_attribute("d"))


def test_qr_code_encodes_the_same_link(ui):
    page = _panel(ui.open())
    expect(page.locator("#subsQr")).to_be_visible()
    assert _qr(page) == BOT  # nothing picked: the bare bot link (shows its own picker)
    expect(page.locator("#subsQr .qr-tile")).to_have_css("opacity", "0.35")
    for picks in ([0, 1], [5], list(range(N))):
        page.evaluate(f"() => localStorage.setItem('{KEY}', '[]')")
        page.reload()
        _panel(page)
        for k in picks:
            page.locator(CHIPS).nth(k).click()
        assert _qr(page) == page.locator(BTN).get_attribute("href")
        assert _qr(page).endswith("?start=" + _mask(picks))
    page.locator("#subsQr").scroll_into_view_if_needed()
    page.wait_for_timeout(500)
    expect(page.locator("#subsQr .qr-tile")).to_have_css("opacity", "1")
    box = page.locator("#subsQr .qr-tile").bounding_box()
    assert box["width"] >= 120


def test_qr_decoder_rejects_a_flipped_module(ui):
    """The test decoder is not a rubber stamp: one flipped data module fails the RS check."""
    page = _panel(ui.open())
    page.locator(CHIPS).nth(0).click()
    svg = page.locator("#qrSvg")
    n = int(svg.get_attribute("data-n"))
    d = svg.locator(".qr-dots").get_attribute("d")
    corner = f"M{n + 3} {n + 3}h1v1h-1z"  # bottom-right module: always data
    bad = d.replace(corner, "") if corner in d else d + corner
    _decode_qr(n, d)
    with pytest.raises(AssertionError):
        _decode_qr(n, bad)


@pytest.mark.parametrize(
    "ctx",
    [{"viewport": MOBILE}, {"viewport": {"width": 1024, "height": 800}, "has_touch": True}],
    ids=["phone", "tablet-touch"],
)
def test_qr_hidden_without_desktop_mouse(ui, ctx):
    page = _panel(ui.open(**ctx))
    expect(page.locator("#subsQr")).to_be_hidden()


# ---------------------------------------------------------------- contrast + wording

CONTRAST_JS = """() => {
  const nums = (s) => { const v = s.match(/[\\d.]+/g).slice(0, 3).map(Number);
    return s.startsWith('color(') ? v.map((c) => c * 255) : v; };  // color(srgb 0-1 ...)
  const lum = ([r, g, b]) => {
    const f = (c) => { c /= 255; return c <= 0.04045 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4; };
    return 0.2126 * f(r) + 0.7152 * f(g) + 0.0722 * f(b);
  };
  const cr = (a, b) => { const x = lum(nums(a)), y = lum(nums(b)); return (Math.max(x, y) + 0.05) / (Math.min(x, y) + 0.05); };
  const stops = (s) => (s.match(/rgba?\\([^)]*\\)/g) || []);
  const cs = (n, p) => getComputedStyle(n, p);
  const out = {};
  const off = document.querySelector('#subsChips .tchip[aria-pressed="false"]:not(.tc-all)');
  out['chip off'] = cr(cs(off).color, cs(off).backgroundColor);
  const on = document.querySelector('#subsChips .tchip[aria-pressed="true"]');
  stops(cs(on, '::before').backgroundImage).forEach((c, k) => { out['chip on ' + k] = cr(cs(on).color, c); });
  const chk = on.querySelector('.tc-ico');
  out['check'] = cr(cs(chk).color, cs(chk).backgroundColor);
  const btn = document.querySelector('#subsBtn');
  stops(cs(btn, '::before').backgroundImage).forEach((c, k) => { out['button ' + k] = cr(cs(btn).color, c); });
  const n = document.querySelector('#subsN');
  out['count'] = cr(cs(n).color, cs(n).backgroundColor);
  const probe = document.createElement('i'); document.body.appendChild(probe);
  probe.style.color = 'var(--surface-solid)'; const surf = cs(probe).color;
  probe.style.color = 'var(--bg)'; const bg = cs(probe).color; probe.remove();
  const help = document.querySelector('.subs-help');
  out['help/surface'] = cr(cs(help).color, surf); out['help/bg'] = cr(cs(help).color, bg);
  const step = document.querySelector('.subs-steps li');
  out['steps/surface'] = cr(cs(step).color, surf);
  const bell = document.querySelector('#bellBtn');
  out['bell'] = cr(cs(bell).color, cs(bell).backgroundColor);
  return out;
}"""
DISABLED_JS = """() => {
  const nums = (s) => { const v = s.match(/[\\d.]+/g).slice(0, 3).map(Number);
    return s.startsWith('color(') ? v.map((c) => c * 255) : v; };  // color(srgb 0-1 ...)
  const lum = ([r, g, b]) => {
    const f = (c) => { c /= 255; return c <= 0.04045 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4; };
    return 0.2126 * f(r) + 0.7152 * f(g) + 0.0722 * f(b);
  };
  const b = document.querySelector('#subsBtn'), s = getComputedStyle(b);
  const x = lum(nums(s.color)), y = lum(nums(s.backgroundColor));
  return (Math.max(x, y) + 0.05) / (Math.min(x, y) + 0.05);
}"""


@pytest.mark.parametrize("theme", ["dark", "light"])
@pytest.mark.parametrize("palette", ["honey", "mint", "periwinkle"])
def test_contrast_in_every_palette(ui, palette, theme):
    page = ui.open(f"?palette={palette}", viewport=DESKTOP)
    if theme == "light":
        page.locator("#themeBtn").click()
        expect(page.locator("html")).to_have_attribute("data-mode", "light")
        page.wait_for_timeout(800)
    _panel(page)
    assert page.evaluate(DISABLED_JS) >= 4.5
    page.locator(CHIPS).nth(0).click()
    page.wait_for_timeout(800)  # colour transitions
    res = page.evaluate(CONTRAST_JS)
    assert {"chip on 0", "chip on 1", "button 0", "button 1"} <= set(res)
    low = {k: round(v, 2) for k, v in res.items() if v < 4.5}
    assert low == {}, (palette, theme, res)


IST_WORD = re.compile(r"\bIST\b")
ALL_TEXT_JS = """() => {
  const out = [document.title, document.body.innerText];
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


def test_forbidden_words_absent(ui):
    page = _panel(ui.open())
    page.locator(ALL).click()
    text = page.evaluate(ALL_TEXT_JS)
    assert not IST_WORD.search(text)
    assert "telegram" not in text.lower()
    assert "free ai" not in text.lower()


def test_panel_text_stays_short(ui):
    page = _panel(ui.open())
    assert len(page.locator(".subs-help").inner_text().split()) <= 8
    expect(page.locator(".subs-steps li:not(.ss-hop)")).to_have_text(
        ["Pick", "Tap Start", "Get alerts"]
    )
    words = page.locator("#subscribe").evaluate(
        """n => { const c = n.cloneNode(true); c.querySelectorAll('.tchip, #subsNote').forEach(x => x.remove());
                  return c.textContent.trim().split(/\\s+/).length; }"""
    )
    assert (
        words <= 35
    )  # +9: the invite-only line. The Security note (5 words) only shows when Security is picked


def test_privacy_section_mentions_subscribers(ui):
    page = ui.open()
    flow = page.locator("#flow")
    expect(flow.locator(".stays .flow-list li")).to_have_count(3)
    expect(flow.locator(".leaves .flow-list li")).to_have_count(3)
    expect(flow.locator(".stays")).to_contain_text(
        "Subscriber chat IDs and chosen topics — nothing else"
    )
    expect(flow.locator(".leaves")).to_contain_text(
        "Finished summary to subscribers’ phones, as private alerts"
    )


@pytest.mark.parametrize("theme", ["dark", "light"])
def test_invite_only_line_under_button(ui, theme):
    page = ui.open()
    if theme == "light":
        page.locator("#themeBtn").click()
        expect(page.locator("html")).to_have_attribute("data-mode", "light")
    line = page.locator("#subsInvite")
    expect(line).to_be_visible()
    expect(line).to_have_text("Invite-only for now — tap Start to ask for access.")
    assert line.evaluate("n => n.tagName") == "P"
    btn_box = page.locator("#subsBtn").bounding_box()
    box = line.bounding_box()
    assert box["y"] >= btn_box["y"] + btn_box["height"]
