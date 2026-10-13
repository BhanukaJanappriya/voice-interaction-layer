"""The confirmation modal (frontend/ui/confirm). Opt in like the rest of
tests/ui. The module is loaded into the running app's page by URL and driven
through window.SLTConfirm; the /confirm endpoints are stubbed, so no backend
state is touched.

Covered: the details, both buttons (Confirm not focused first), Escape,
the focus trap, the countdown ending in an expiry, the accept and cancel
requests, a result decided by the server (resolve), the status line, 360 px
width, a light and dark theme, reduced motion, no console errors, and axe.
"""

import json

import pytest

from ui_helpers import HEIGHTS, shot

SAMPLE = {
    "id": "tok-abc123",
    "tool": "purchase_extra_gb",
    "title": "Buy an extra data package",
    "lines": [
        {"label": "Package", "value": "Extra 10GB"},
        {"label": "Price", "value": "490.0"},
        {"label": "Validity", "value": "30 days"},
        {"label": "Connection", "value": "ending 678"},
    ],
    "expires_in": 60,
    "state": "pending",
}

HOOK = """
() => {
  window.__events = [];
  for (const name of ["slt:confirmation-result", "slt:confirmation-closed"]) {
    document.addEventListener(name, (e) => window.__events.push({ name, detail: e.detail }));
  }
}
"""

MODAL = ".slt-confirm-card"
ENDPOINTS = __import__("re").compile(r"/confirm/[^/]+/(accept|cancel)$")


def load(page, base_url, errors, accept=None, cancel=None, delay_ms=0):
    """Opens the app page, loads the module and stubs the two endpoints.
    accept and cancel are the JSON bodies the stub answers with."""
    requests = []

    def handler(route, request):
        requests.append((request.method, request.url.split("/confirm/", 1)[1], request.post_data))
        path = request.url
        body = accept if path.endswith("/accept") else cancel
        route.fulfill(
            status=200,
            content_type="application/json",
            headers={"Cache-Control": "no-store"},
            body=json.dumps(body or {"kind": "cancelled", "text": "I cancelled that. Nothing was changed.", "state": "cancelled"}),
        )

    page.route(ENDPOINTS, handler)
    # Blocked CDN hosts (three.js, fonts) print "Failed to load resource": not ours.
    page.on("console", lambda m: errors.append(m.text) if m.type == "error" and not m.text.startswith("Failed to load resource") else None)
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.goto(base_url + "/", wait_until="domcontentloaded")
    page.add_style_tag(url=base_url + "/ui/confirm/confirm.css")
    page.add_script_tag(url=base_url + "/ui/confirm/confirm.js")
    page.wait_for_function("() => !!window.SLTConfirm")
    page.evaluate(HOOK)
    return requests


def open_modal(page, confirmation=None):
    page.evaluate("(c) => window.SLTConfirm.show(c)", confirmation or SAMPLE)
    page.wait_for_selector(MODAL, state="visible")


def events(page):
    return page.evaluate("() => window.__events")


@pytest.fixture
def errors():
    return []


def test_details_buttons_and_no_initial_focus(make_page, base_url, errors):
    page = make_page(390, HEIGHTS[390])
    load(page, base_url, errors)
    open_modal(page)
    assert page.get_attribute(MODAL, "role") == "dialog"
    assert page.get_attribute(MODAL, "aria-modal") == "true"
    assert page.inner_text(".slt-confirm-title") == SAMPLE["title"]
    rows = page.eval_on_selector_all(".slt-confirm-row", "(rows) => rows.map((r) => [r.children[0].textContent, r.children[1].textContent])")
    assert rows == [[line["label"], line["value"]] for line in SAMPLE["lines"]]
    assert page.is_visible(".slt-confirm-yes") and page.is_visible(".slt-confirm-no")
    assert page.inner_text(".slt-confirm-yes") == "Confirm" and page.inner_text(".slt-confirm-no") == "Cancel"
    focused = page.evaluate("() => document.activeElement && document.activeElement.className")
    assert "slt-confirm-yes" not in focused
    assert "Expires in 1:00" in page.inner_text(".slt-confirm-timer") or "Expires in 0:5" in page.inner_text(".slt-confirm-timer")
    assert not errors


def test_the_countdown_runs_and_ends_in_an_expiry(make_page, base_url, errors):
    page = make_page(390, HEIGHTS[390])
    requests = load(page, base_url, errors)
    open_modal(page, dict(SAMPLE, expires_in=2))
    page.wait_for_function("() => window.__events.some((e) => e.name === 'slt:confirmation-result')", timeout=8000)
    result = [e for e in events(page) if e["name"] == "slt:confirmation-result"][0]["detail"]
    assert result["kind"] == "expired" and "Nothing was changed" in result["text"]
    assert "timed out" in page.inner_text(".slt-confirm-result")
    page.wait_for_selector(MODAL, state="detached", timeout=8000)
    assert any(e["name"] == "slt:confirmation-closed" for e in events(page))
    assert requests == []  # an expiry asks the server for nothing
    assert not errors


def test_confirm_posts_once_and_reports_the_hub_text(make_page, base_url, errors):
    page = make_page(390, HEIGHTS[390])
    body = {"kind": "executed", "text": '{"success": true, "message": "Extra GB purchase submitted"}', "state": "executed"}
    requests = load(page, base_url, errors, accept=body)
    open_modal(page)
    page.click(".slt-confirm-yes")
    page.wait_for_function("() => window.__events.some((e) => e.name === 'slt:confirmation-result')")
    detail = [e for e in events(page) if e["name"] == "slt:confirmation-result"][0]["detail"]
    assert detail == {"kind": "executed", "text": body["text"]}
    assert "Extra GB purchase submitted" in page.inner_text(".slt-confirm-result")
    assert requests == [("POST", "tok-abc123/accept", None)]
    assert not page.is_visible(".slt-confirm-yes")
    page.wait_for_selector(MODAL, state="detached", timeout=10000)
    assert not errors


def test_cancel_button_and_escape_cancel(make_page, base_url, errors):
    page = make_page(390, HEIGHTS[390])
    requests = load(page, base_url, errors)
    open_modal(page)
    page.click(".slt-confirm-no")
    page.wait_for_function("() => window.__events.some((e) => e.name === 'slt:confirmation-result')")
    page.wait_for_selector(MODAL, state="detached", timeout=8000)
    assert requests[0][1] == "tok-abc123/cancel"
    page.evaluate("() => { window.__events.length = 0; }")
    open_modal(page, dict(SAMPLE, id="tok-second"))
    page.keyboard.press("Escape")
    page.wait_for_function("() => window.__events.some((e) => e.name === 'slt:confirmation-result')")
    assert requests[1][1] == "tok-second/cancel"
    assert [e for e in events(page) if e["name"] == "slt:confirmation-result"][0]["detail"]["kind"] == "cancelled"
    assert not errors


def test_the_focus_stays_inside_the_dialog(make_page, base_url, errors):
    page = make_page(390, HEIGHTS[390])
    load(page, base_url, errors)
    page.evaluate("() => { const b = document.createElement('button'); b.id = 'outside'; b.textContent = 'outside'; document.body.prepend(b); b.focus(); }")
    open_modal(page)
    seen = []
    for key in ["Tab"] * 5 + ["Shift+Tab"] * 5:
        page.keyboard.press(key)
        seen.append(key[0] + page.evaluate("() => (document.activeElement.className.match(/slt-confirm-(yes|no)/) || ['?'])[0]"))
        assert page.evaluate("() => !!document.activeElement.closest('.slt-confirm-card')"), str(seen)
    page.keyboard.press("Escape")
    page.wait_for_selector(MODAL, state="detached", timeout=8000)
    assert page.evaluate("() => document.activeElement.id") == "outside"  # focus goes back


def test_a_result_decided_by_the_server_closes_the_modal(make_page, base_url, errors):
    page = make_page(390, HEIGHTS[390])
    requests = load(page, base_url, errors)
    open_modal(page)
    page.evaluate("() => window.SLTConfirm.resolve('executed', 'Done by voice')")
    assert "Done by voice" in page.inner_text(".slt-confirm-result")
    detail = [e for e in events(page) if e["name"] == "slt:confirmation-result"][0]["detail"]
    assert detail == {"kind": "executed", "text": "Done by voice"}
    page.wait_for_selector(MODAL, state="detached", timeout=10000)
    assert requests == []
    # hide() closes at once and sends nothing
    open_modal(page, dict(SAMPLE, id="tok-third"))
    page.evaluate("() => window.SLTConfirm.hide()")
    page.wait_for_selector(MODAL, state="detached")
    assert requests == [] and not errors


def test_a_failed_request_never_claims_nothing_changed(make_page, base_url, errors):
    page = make_page(390, HEIGHTS[390])
    load(page, base_url, errors)
    page.unroute(ENDPOINTS)
    page.route(ENDPOINTS, lambda route: route.abort())
    open_modal(page)
    page.click(".slt-confirm-yes")
    page.wait_for_function("() => window.__events.some((e) => e.name === 'slt:confirmation-result')")
    detail = [e for e in events(page) if e["name"] == "slt:confirmation-result"][0]["detail"]
    assert detail["kind"] == "failed" and "could not tell" in detail["text"]


def test_the_status_line_is_a_live_region(make_page, base_url, errors):
    page = make_page(390, HEIGHTS[390])
    load(page, base_url, errors)
    page.evaluate("() => window.SLTConfirm.setStatus('Checking your balance')")
    assert page.get_attribute("#slt-confirm-status", "aria-live") == "polite"
    assert page.inner_text("#slt-confirm-status") == "Checking your balance"
    assert page.is_visible("#slt-confirm-status")
    page.evaluate("() => window.SLTConfirm.setStatus(null)")
    assert not page.is_visible("#slt-confirm-status")


@pytest.mark.parametrize("theme", ["light", "dark"])
def test_it_fits_at_360_px_with_long_text(make_page, base_url, errors, theme):
    page = make_page(360, HEIGHTS[360], theme)
    load(page, base_url, errors)
    long_lines = SAMPLE["lines"] + [{"label": "Problem", "value": "The internet keeps dropping every few minutes since yesterday evening " * 2}]
    open_modal(page, dict(SAMPLE, lines=long_lines, title="අමතර දත්ත පැකේජයක් මිලදී ගැනීම"))
    page.wait_for_timeout(400)
    box = page.eval_on_selector(MODAL, "(n) => { const r = n.getBoundingClientRect(); return [r.left, r.right, document.documentElement.scrollWidth, innerWidth]; }")
    assert box[0] >= 0 and box[1] <= 360 and box[2] <= box[3]
    for selector in (".slt-confirm-yes", ".slt-confirm-no"):
        bounds = page.eval_on_selector(selector, "(n) => { const r = n.getBoundingClientRect(); return [r.left, r.right, r.height]; }")
        assert bounds[0] >= 0 and bounds[1] <= 360 and bounds[2] >= 44
    shot(page, f"confirm-{theme}-360")
    assert not errors


def test_reduced_motion_still_works(make_page, base_url, errors):
    page = make_page(390, HEIGHTS[390], reduced_motion="reduce")
    load(page, base_url, errors)
    open_modal(page)
    assert page.is_visible(".slt-confirm-yes")
    assert not errors


def test_axe_finds_no_serious_violations_in_the_modal(make_page, base_url, errors):
    axe_mod = pytest.importorskip("axe_playwright_python.sync_playwright", reason="axe-playwright-python is not installed")
    page = make_page(360, HEIGHTS[360], "light", reduced_motion="reduce")
    load(page, base_url, errors)
    open_modal(page)
    page.wait_for_timeout(400)
    results = axe_mod.Axe().run(page, context=".slt-confirm-overlay").response
    bad = [(v["id"], [str(n["target"][0]) for n in v["nodes"][:2]]) for v in results.get("violations", []) if v.get("impact") in ("serious", "critical")]
    assert not bad, bad
