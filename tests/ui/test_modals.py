"""Popups: header icon badge, wizard progress indicator, and the wizard run
end to end in English, Sinhala and Tamil against the real backend.

Detection: the badge and progress tests skip until window.SltUi.badge
exists (badge) or the root has the ui-on class (progress). The end to end
wizard test runs on any frontend.

Rate limit hygiene: the wizard allows one code per number every 30 s and 6
per number per hour, so the end to end test uses a different seeded number
per language and sends exactly one code per run.
"""

import re

import pytest

from ui_helpers import CUSTOMERS, HEIGHTS, open_screen, shot, ui_on

# screen name -> the container that must carry the badge
BADGE_TARGETS = {
    "mic": "#mic-permission-modal .modal-card",
    "history": "#history-panel .modal-card",
    "wizard1": ".slt-verify-card",
    "wizard2": ".slt-verify-card",
    "wizard3": ".slt-verify-card",
    "wizard_invalid_details": ".slt-verify-card",
    "wizard_rate_limited": ".slt-verify-card",
    "wizard_no_contact": ".slt-verify-card",
    "wizard_wrong_code": ".slt-verify-card",
    "wizard_locked": ".slt-verify-card",
    "wizard_network": ".slt-verify-card",
}

BADGE_JS = """
(sel) => {
  const box = document.querySelector(sel);
  if (!box) return { box: false };
  const badges = [...box.querySelectorAll('[class*="badge" i]')].filter((b) => b.querySelector('svg'));
  return { box: true, count: badges.length, visible: badges.some((b) => b.getBoundingClientRect().width > 0) };
}
"""


@pytest.mark.parametrize("screen", list(BADGE_TARGETS))
def test_popup_has_header_icon_badge(make_page, base_url, screen):
    page = make_page(390, HEIGHTS[390], "light")
    page.goto(base_url + "/", wait_until="domcontentloaded")
    if not page.evaluate("() => !!(window.SltUi && typeof window.SltUi.badge === 'function')"):
        pytest.skip("SltUi.badge is not integrated yet (no icon badge component on the page)")
    open_screen(page, base_url, screen)
    page.wait_for_timeout(500)  # badge animation
    shot(page, f"badge-{screen}")
    info = page.evaluate(BADGE_JS, BADGE_TARGETS[screen])
    assert info["box"], f"{screen}: popup container {BADGE_TARGETS[screen]} not found"
    assert info["count"] >= 1 and info["visible"], f"{screen}: no visible icon badge with an svg in the popup"


PROGRESS_RE = {
    1: re.compile(r"\b1\s*(of|/)\s*3\b"),
    2: re.compile(r"\b2\s*(of|/)\s*3\b"),
    3: re.compile(r"\b3\s*(of|/)\s*3\b"),
}


def test_wizard_progress_indicator(make_page, base_url):
    page = make_page(390, HEIGHTS[390], "light")
    page.goto(base_url + "/", wait_until="domcontentloaded")
    if not ui_on(page):
        pytest.skip("the new UI layer is not integrated yet (no ui-on class), so no progress indicator")
    seen = {}
    for step, screen in ((1, "wizard1"), (2, "wizard2"), (3, "wizard3")):
        open_screen(page, base_url, screen)
        page.wait_for_timeout(350)
        text = page.inner_text(".slt-verify-card")
        seen[step] = bool(PROGRESS_RE[step].search(text))
    assert all(seen.values()), f"progress text '{{n}} of 3' missing on steps: {[k for k, v in seen.items() if not v]}"


def _run_wizard(page, app_server, lang):
    name, phone = CUSTOMERS[lang]
    page.goto(app_server.base_url + "/", wait_until="domcontentloaded")
    from ui_helpers import settle

    settle(page)
    page.click(f'#screen-language [data-language="{lang}"]')
    page.wait_for_selector("#screen-select.active")
    page.click("#go-chat")
    page.wait_for_selector("#screen-user.active")
    page.click("#go-existing")
    page.wait_for_selector(".slt-verify-card input[name=name]")
    page.fill(".slt-verify-card input[name=name]", name)
    page.fill(".slt-verify-card input[name=phone]", phone)
    page.check(".slt-verify-card .slt-v-check")
    page.click(".slt-verify-card button[type=submit]")
    page.wait_for_selector(".slt-verify-card .slt-v-radio", timeout=20000)
    offset = app_server.log_size()
    page.click(".slt-verify-card button[type=submit]")
    page.wait_for_selector(".slt-verify-card .slt-v-digit", timeout=20000)
    code = app_server.wait_for_code(offset)
    for index, digit in enumerate(code):
        page.fill(f".slt-verify-card .slt-v-digit >> nth={index}", digit)
    page.click(".slt-verify-card button[type=submit]")
    page.wait_for_selector(".slt-verify-overlay", state="detached", timeout=20000)
    page.wait_for_selector("#screen-chat.active", timeout=20000)


@pytest.mark.parametrize("lang", ["en", "si", "ta"])
def test_wizard_completes_end_to_end(make_page, app_server, lang):
    page = make_page(390, HEIGHTS[390], "light", timeout=30000)
    _run_wizard(page, app_server, lang)
    assert page.evaluate("() => document.documentElement.lang") == lang
