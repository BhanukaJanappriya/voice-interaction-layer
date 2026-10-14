"""The rating window (frontend/ui/rating). Opt in like the rest of tests/ui.
The module is loaded into the running app's page by URL and driven through
window.SLTRating; the /feedback endpoints are stubbed.
"""

import json

import pytest

from ui_helpers import HEIGHTS

HOOK = """
() => {
  window.__events = [];
  document.addEventListener("slt:rating-closed", (e) => window.__events.push(e.detail));
}
"""
MODAL = ".slt-rating-card"
ASK = "(o) => { window.__result = null; window.SLTRating.ask(o).then((r) => { window.__result = r; }); }"
OPTS = {"channel": "chat", "language": "en", "turns": 3}


class Posts(list):
    config_hits = None


def load(page, base_url, errors, config=None, rating_status=200, abort_rating=False):
    posts = Posts()
    config_hits = []

    def on_config(route, request):
        config_hits.append(request.url)
        route.fulfill(status=200, content_type="application/json", body=json.dumps(config or {"enabled": True}))

    def on_rating(route, request):
        if abort_rating:
            route.abort()
            return
        posts.append((request.method, request.post_data, request.headers.get("content-type")))
        route.fulfill(status=rating_status, content_type="application/json", body='{"ok": true}')

    page.route("**/feedback/config", on_config)
    page.route("**/feedback/rating", on_rating)
    page.on("console", lambda m: errors.append(m.text) if m.type == "error" and not m.text.startswith("Failed to load resource") else None)
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.goto(base_url + "/", wait_until="domcontentloaded")
    page.add_style_tag(url=base_url + "/ui/rating/rating.css")
    page.add_script_tag(url=base_url + "/ui/rating/rating.js")
    page.wait_for_function("() => !!window.SLTRating")
    page.evaluate(HOOK)
    posts.config_hits = config_hits
    return posts


def open_modal(page, opts=None):
    page.evaluate(ASK, opts or OPTS)
    page.wait_for_selector(MODAL, state="visible")


def result(page):
    page.wait_for_function("() => window.__result !== null && window.__result !== undefined")
    return page.evaluate("() => window.__result")


@pytest.fixture
def errors():
    return []




def test_appears_with_turns_and_not_without(make_page, base_url, errors):
    page = make_page(390, HEIGHTS[390])
    load(page, base_url, errors)
    page.evaluate(ASK, dict(OPTS, turns=0))
    assert result(page) == {"rated": False, "reason": "no_turns"}
    assert page.locator(MODAL).count() == 0
    open_modal(page)
    assert page.get_attribute(MODAL, "role") == "dialog"
    assert page.get_attribute(MODAL, "aria-modal") == "true"
    assert page.inner_text(".slt-rating-title") == "How was your experience?"
    assert "chat" in page.inner_text(".slt-rating-line")
    assert page.get_attribute(".slt-rating-stars", "role") == "radiogroup"
    assert page.locator("[role=radio]").count() == 5
    assert page.get_attribute("[role=radio] >> nth=2", "aria-label") == "3 of 5 stars"
    assert not errors


def test_submit_is_disabled_until_a_star_is_chosen(make_page, base_url, errors):
    page = make_page(390, HEIGHTS[390])
    load(page, base_url, errors)
    open_modal(page)
    assert page.is_disabled(".slt-rating-submit")
    page.click("[role=radio] >> nth=3")
    assert page.is_enabled(".slt-rating-submit")
    assert page.get_attribute("[role=radio] >> nth=3", "aria-checked") == "true"
    assert page.inner_text(".slt-rating-label") == "Very good"
    on = page.eval_on_selector_all(".slt-rating-star.is-on", "(n) => n.length")
    assert on == 4


def test_keyboard_selection(make_page, base_url, errors):
    page = make_page(390, HEIGHTS[390])
    load(page, base_url, errors)
    open_modal(page)
    page.keyboard.press("Tab")  # first star
    assert page.evaluate("() => document.activeElement.getAttribute('role')") == "radio"
    page.keyboard.press("ArrowRight")
    page.keyboard.press("ArrowRight")
    assert page.get_attribute("[role=radio] >> nth=2", "aria-checked") == "true"
    assert page.inner_text(".slt-rating-label") == "Good"
    page.keyboard.press("ArrowLeft")
    assert page.get_attribute("[role=radio] >> nth=1", "aria-checked") == "true"
    page.keyboard.press("End")
    assert page.get_attribute("[role=radio] >> nth=4", "aria-checked") == "true"
    page.keyboard.press("Home")
    assert page.get_attribute("[role=radio] >> nth=0", "aria-checked") == "true"
    page.keyboard.press("ArrowUp")
    page.keyboard.press("ArrowDown")
    assert page.get_attribute("[role=radio] >> nth=0", "aria-checked") == "true"
    # roving tabindex: only the checked star is in the tab order
    assert page.eval_on_selector_all("[role=radio]", "(n) => n.map((x) => x.tabIndex)") == [0, -1, -1, -1, -1]
    page.keyboard.press("ArrowRight")
    page.keyboard.press("ArrowRight")
    page.keyboard.press("Space")
    assert page.get_attribute("[role=radio] >> nth=2", "aria-checked") == "true"
    page.keyboard.press("Enter")
    assert page.get_attribute("[role=radio] >> nth=2", "aria-checked") == "true"
    assert not errors


def test_submit_sends_exactly_the_payload_once_and_thanks(make_page, base_url, errors):
    page = make_page(390, HEIGHTS[390])
    posts = load(page, base_url, errors)
    open_modal(page, {"channel": "voice", "language": "si", "turns": 4})
    assert "voice" in page.inner_text(".slt-rating-line")
    page.click("[role=radio] >> nth=4")
    page.click(".slt-rating-submit")
    page.wait_for_selector(".slt-rating-message.is-ok", state="visible")
    assert "Thank you" in page.inner_text(".slt-rating-message")
    page.wait_for_selector(MODAL, state="detached", timeout=6000)
    assert len(posts) == 1
    assert posts[0][0] == "POST" and "json" in posts[0][2]
    assert json.loads(posts[0][1]) == {"rating": 5, "channel": "voice", "language": "si", "turns": 4}
    assert result(page) == {"rated": True, "rating": 5}
    assert page.evaluate("() => window.__events") == [{"rated": True, "rating": 5}]
    assert not errors


def test_skip_and_escape_send_nothing(make_page, base_url, errors):
    page = make_page(390, HEIGHTS[390])
    posts = load(page, base_url, errors)
    page.evaluate("() => { const b = document.createElement('button'); b.id = 'outside'; document.body.prepend(b); b.focus(); }")
    open_modal(page)
    page.click("[role=radio] >> nth=1")
    page.click(".slt-rating-skip")
    page.wait_for_selector(MODAL, state="detached")
    assert result(page) == {"rated": False, "reason": "skipped"}
    assert page.evaluate("() => document.activeElement.id") == "outside"
    # a second page session, to test Escape and the backdrop
    page2 = make_page(390, HEIGHTS[390])
    load(page2, base_url, errors)
    open_modal(page2)
    page2.keyboard.press("Escape")
    page2.wait_for_selector(MODAL, state="detached")
    assert result(page2) == {"rated": False, "reason": "skipped"}
    page3 = make_page(390, HEIGHTS[390])
    posts3 = load(page3, base_url, errors)
    open_modal(page3)
    # the landing page may sit above the overlay in this harness; the real session has none
    page3.add_style_tag(content=".slt-landing, [class^=lnd] { display: none !important; }")
    page3.mouse.click(5, 5)
    page3.wait_for_selector(MODAL, state="detached")
    assert result(page3) == {"rated": False, "reason": "skipped"}
    assert posts == [] and posts3 == []
    assert not errors


def test_failure_closes_and_does_not_hang(make_page, base_url, errors):
    page = make_page(390, HEIGHTS[390])
    load(page, base_url, errors, abort_rating=True)
    open_modal(page)
    page.click("[role=radio] >> nth=2")
    page.click(".slt-rating-submit")
    page.wait_for_selector(".slt-rating-message.is-failed", state="visible")
    assert "Could not send" in page.inner_text(".slt-rating-message")
    page.wait_for_selector(MODAL, state="detached", timeout=6000)
    assert result(page)["rated"] is False
    assert page.evaluate("() => document.body.style.overflow") != "hidden"
    # a server error behaves the same
    page2 = make_page(390, HEIGHTS[390])
    load(page2, base_url, errors, rating_status=500)
    open_modal(page2)
    page2.click("[role=radio] >> nth=0")
    page2.click(".slt-rating-submit")
    page2.wait_for_selector(MODAL, state="detached", timeout=6000)
    assert not errors


def test_second_ask_in_the_same_session_does_not_reappear(make_page, base_url, errors):
    page = make_page(390, HEIGHTS[390])
    load(page, base_url, errors)
    open_modal(page)
    page.keyboard.press("Escape")
    page.wait_for_selector(MODAL, state="detached")
    page.evaluate(ASK, OPTS)
    assert result(page) == {"rated": False, "reason": "already_asked"}
    assert page.locator(MODAL).count() == 0
    # the other channel is still asked
    open_modal(page, dict(OPTS, channel="voice"))
    page.keyboard.press("Escape")
    page.wait_for_selector(MODAL, state="detached")


def test_disabled_config_shows_nothing_and_is_cached(make_page, base_url, errors):
    page = make_page(390, HEIGHTS[390])
    posts = load(page, base_url, errors, config={"enabled": False})
    page.evaluate(ASK, OPTS)
    assert result(page) == {"rated": False, "reason": "disabled"}
    page.evaluate(ASK, dict(OPTS, channel="voice"))
    page.wait_for_timeout(200)
    assert page.locator(MODAL).count() == 0
    assert len(posts.config_hits) == 1


def test_a_failing_config_request_still_shows_the_window(make_page, base_url, errors):
    page = make_page(390, HEIGHTS[390])
    load(page, base_url, errors)
    page.unroute("**/feedback/config")
    page.route("**/feedback/config", lambda route: route.abort())
    open_modal(page)
    assert page.is_visible(MODAL)


@pytest.mark.parametrize("theme", ["light", "dark"])
def test_it_fits_at_360_px(make_page, base_url, errors, theme):
    page = make_page(360, HEIGHTS[360], theme)
    load(page, base_url, errors)
    open_modal(page)
    page.wait_for_timeout(400)
    box = page.eval_on_selector(MODAL, "(n) => { const r = n.getBoundingClientRect(); return [r.left, r.right, r.top, r.bottom, document.documentElement.scrollWidth, innerWidth, innerHeight]; }")
    assert box[0] >= 0 and box[1] <= 360 and box[4] <= box[5] and box[2] >= 0 and box[3] <= box[6]
    sizes = page.eval_on_selector_all("[role=radio], .slt-rating-submit, .slt-rating-skip", "(n) => n.map((x) => { const r = x.getBoundingClientRect(); return [r.width, r.height, r.left, r.right]; })")
    for w, h, left, right in sizes:
        assert h >= 44 and w >= 44 and left >= 0 and right <= 360
    page.click("[role=radio] >> nth=2")
    on = page.eval_on_selector(".slt-rating-star.is-on", "(n) => getComputedStyle(n).color")
    off = page.eval_on_selector("[role=radio] >> nth=4", "(n) => getComputedStyle(n).color")
    assert on != off
    assert not errors


def test_focus_is_trapped_and_returns(make_page, base_url, errors):
    page = make_page(390, HEIGHTS[390])
    load(page, base_url, errors)
    page.evaluate("() => { const b = document.createElement('button'); b.id = 'outside'; document.body.prepend(b); b.focus(); }")
    open_modal(page)
    page.click("[role=radio] >> nth=1")
    for key in ["Tab"] * 5 + ["Shift+Tab"] * 5:
        page.keyboard.press(key)
        assert page.evaluate("() => !!document.activeElement.closest('.slt-rating-card')")
    page.keyboard.press("Escape")
    page.wait_for_selector(MODAL, state="detached")
    assert page.evaluate("() => document.activeElement.id") == "outside"


def test_reduced_motion_still_works(make_page, base_url, errors):
    page = make_page(390, HEIGHTS[390], reduced_motion="reduce")
    load(page, base_url, errors)
    open_modal(page)
    assert page.is_visible(".slt-rating-submit")
    assert not errors


def test_axe_finds_no_serious_violations(make_page, base_url, errors):
    axe_mod = pytest.importorskip("axe_playwright_python.sync_playwright", reason="axe-playwright-python is not installed")
    page = make_page(360, HEIGHTS[360], "light", reduced_motion="reduce")
    load(page, base_url, errors)
    open_modal(page)
    page.click("[role=radio] >> nth=2")
    page.wait_for_timeout(400)
    results = axe_mod.Axe().run(page, context=".slt-rating-overlay").response
    bad = [(v["id"], [str(n["target"][0]) for n in v["nodes"][:2]]) for v in results.get("violations", []) if v.get("impact") in ("serious", "critical")]
    assert not bad, bad
