"""Shared helpers for the browser tests in tests/ui.

Everything here works on the DOM of the page as it is served, so the same
helpers drive the current frontend and the one with the new UI layer.
Features of the new layer are detected (window.SltUi, the ui-on class on
the root element), never assumed.
"""

import json
import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
SHOT_DIR = REPO_ROOT / "docs" / "ui-after"

WIDTHS = [360, 390, 768, 1024, 1440]
HEIGHTS = {360: 740, 390: 844, 768: 1024, 1024: 768, 1440: 900}
THEMES = ["light", "dark"]

# Fake directory rows seeded by python -m verification.seed_demo.
CUSTOMERS = {
    "en": ("Test Customer", "011 200 0001"),
    "si": ("Test Sinhala Customer", "011 200 0002"),
    "ta": ("Test Email Only", "011 200 0003"),
}

# Hosts the page loads from the network. They are blocked in the tests so a
# run is fast and does not depend on the internet; the page has fallbacks
# for all of them (flat avatar, system fonts).
BLOCKED_HOSTS = re.compile(r"https?://(cdn\.jsdelivr\.net|fonts\.googleapis\.com|fonts\.gstatic\.com)/")

LANDING_JS = """
() => {
  const vw = innerWidth, vh = innerHeight;
  const nodes = document.querySelectorAll('[class*="landing" i], [id*="landing" i]');
  for (const n of nodes) {
    const cs = getComputedStyle(n);
    if (cs.display === 'none' || cs.visibility === 'hidden') continue;
    if (cs.position !== 'fixed' && cs.position !== 'absolute') continue;
    const r = n.getBoundingClientRect();
    if (r.width >= vw * 0.8 && r.height >= vh * 0.8 && parseFloat(cs.opacity) > 0.01) return true;
  }
  return false;
}
"""

# Records when the landing overlay appears and disappears, from page load.
LANDING_PROBE = """
(() => {
  window.__landing = { start: null, end: null };
  const t = () => performance.now();
  const isLanding = (n) => n && n.nodeType === 1 &&
    /landing/i.test((n.id || '') + ' ' + (typeof n.className === 'string' ? n.className : ''));
  const mo = new MutationObserver((records) => {
    for (const r of records) {
      r.addedNodes.forEach((n) => { if (isLanding(n) && window.__landing.start === null) window.__landing.start = t(); });
      r.removedNodes.forEach((n) => { if (isLanding(n) && window.__landing.start !== null && window.__landing.end === null) window.__landing.end = t(); });
    }
  });
  // Observe the document node itself: it exists before the page starts, and
  // the landing is added while the page is still being parsed.
  mo.observe(document, { childList: true, subtree: true });
})();
"""


def has_ui_layer(page):
    return bool(page.evaluate("() => !!window.SltUi"))


def has_landing(page):
    return bool(page.evaluate("() => !!(window.SltUi && window.SltUi.landing && typeof window.SltUi.landing.run === 'function')"))


def ui_on(page):
    return bool(page.evaluate("() => document.documentElement.classList.contains('ui-on')"))


def landing_visible(page):
    return bool(page.evaluate(LANDING_JS))


def skip_landing(page, timeout=6000):
    """If a landing overlay is up, skip it with a key press and wait for it
    to go. A no-op on a frontend without the landing."""
    try:
        page.wait_for_timeout(150)
        if landing_visible(page):
            page.keyboard.press("Escape")
            page.wait_for_function("() => !((" + LANDING_JS.strip() + ")())", timeout=timeout)
    except Exception:
        pass


def shot(page, name):
    """Writes docs/ui-after/NAME-THEME-WIDTH.png (gitignored)."""
    SHOT_DIR.mkdir(parents=True, exist_ok=True)
    theme = getattr(page, "ui_theme", "light")
    width = page.viewport_size["width"] if page.viewport_size else 0
    path = SHOT_DIR / f"{name}-{theme}-{width}.png"
    try:
        page.screenshot(path=str(path))
    except Exception:
        # A screenshot must never fail a layout check; it is evidence only.
        pass
    return path


# Wizard network stubs (layout, modal and axe tests). The end to end test in
# test_modals.py uses the real backend instead.

METHODS_BOTH = [
    {"id": "sms", "masked": "07X XXX X123"},
    {"id": "email", "masked": "XXXXXXXXXXXXXX@example.invalid"},
]


def stub_verify(page, scenario="ok"):
    """Routes /verify/* to canned answers.

    scenario: ok | invalid_details | rate_limited | no_contact | wrong_code |
              locked | network
    """

    def fulfill(route, status, body, headers=None):
        route.fulfill(status=status, content_type="application/json", body=json.dumps(body), headers=headers or {})

    def handler(route):
        url = route.request.url
        if scenario == "network":
            return route.abort()
        if url.endswith("/verify/lookup"):
            if scenario == "invalid_details":
                return fulfill(route, 422, {"code": "invalid_details"})
            if scenario == "rate_limited":
                return fulfill(route, 429, {"code": "rate_limited", "retry_after": 1800}, {"Retry-After": "1800"})
            if scenario == "no_contact":
                return fulfill(route, 200, {"ticket": "stub-ticket", "methods": []})
            return fulfill(route, 200, {"ticket": "stub-ticket", "methods": METHODS_BOTH})
        if url.endswith("/verify/otp/send"):
            return fulfill(route, 200, {"challenge_id": "stub-challenge", "expires_in": 120, "resend_in": 30, "masked": "07X XXX X123"})
        if url.endswith("/verify/otp/verify"):
            if scenario == "locked":
                return fulfill(route, 423, {"code": "locked"})
            return fulfill(route, 401, {"code": "invalid", "attempts_left": 2})
        return route.continue_()

    page.route("**/verify/**", handler)


# Navigation. Every helper starts from a fresh load of the app.


def settle(page):
    skip_landing(page)
    page.wait_for_selector("#screen-language.active", state="attached")


def open_app(page, base_url, lang="en"):
    page.goto(base_url + "/", wait_until="domcontentloaded")
    settle(page)


def _choose_language(page, lang):
    page.click(f'#screen-language [data-language="{lang}"]')
    page.wait_for_selector("#screen-select.active")


def _fill_step1(page, lang, terms=True):
    name, phone = CUSTOMERS[lang]
    page.fill(".slt-verify-card input[name=name]", name)
    page.fill(".slt-verify-card input[name=phone]", phone)
    if terms:
        page.check(".slt-verify-card .slt-v-check")


def open_screen(page, base_url, screen, lang="en"):
    """Brings the page to the named screen or popup.

    screens: language select user mic voice history chat wizard1 wizard2
             wizard3 and the stubbed error states wizard_invalid_details,
             wizard_rate_limited, wizard_no_contact, wizard_wrong_code,
             wizard_locked, wizard_network
    """
    open_app(page, base_url, lang)
    if screen == "language":
        return
    _choose_language(page, lang)
    if screen == "select":
        return
    wizard = screen.startswith("wizard")
    page.click("#go-chat" if screen == "chat" else "#go-voice")
    page.wait_for_selector("#screen-user.active")
    if screen == "user":
        return
    if wizard:
        scenario = "ok"
        if screen.startswith("wizard_"):
            scenario = screen[len("wizard_"):]
        stub_verify(page, scenario)
        page.click("#go-existing")
        page.wait_for_selector(".slt-verify-card input[name=name]")
        if screen == "wizard1":
            return
        _fill_step1(page, lang)
        if scenario == "network":
            page.unroute("**/verify/**")
            stub_verify(page, "network")
        page.click(".slt-verify-card button[type=submit]")
        if scenario in ("invalid_details", "rate_limited", "network"):
            page.wait_for_selector(".slt-verify-card .slt-v-error:not(.hidden)")
            return
        page.wait_for_selector(".slt-verify-card .slt-v-radio, .slt-verify-card .slt-v-error")
        if screen == "wizard2" or scenario == "no_contact":
            return
        page.click(".slt-verify-card button[type=submit]")
        page.wait_for_selector(".slt-verify-card .slt-v-digit")
        if screen == "wizard3":
            return
        for index, digit in enumerate("123456"):
            page.fill(f".slt-verify-card .slt-v-digit >> nth={index}", digit)
        page.click(".slt-verify-card button[type=submit]")
        page.wait_for_selector(".slt-verify-card .slt-v-error:not(.hidden)")
        return
    page.click("#go-new")
    if screen == "chat":
        page.wait_for_selector("#screen-chat.active")
        return
    page.wait_for_selector("#screen-voice.active")
    page.wait_for_selector("#mic-permission-modal:not(.hidden)")
    if screen == "mic":
        return
    page.click("#dismiss-mic-modal")
    page.wait_for_selector("#mic-permission-modal.hidden", state="attached")
    if screen == "voice":
        return
    if screen == "history":
        # On a phone the History button lives in the More sheet.
        if not page.is_visible("#history-toggle") and page.is_visible(".dash-more"):
            page.click(".dash-more")
            page.wait_for_selector("#history-toggle", state="visible")
        page.click("#history-toggle")
        page.wait_for_selector("#history-panel:not(.hidden)")
        return
    raise ValueError(f"unknown screen {screen}")


# Layout measurements

OVERFLOW_JS = """
() => {
  const de = document.documentElement, body = document.body;
  const vw = de.clientWidth;
  const out = { scrollWidth: de.scrollWidth, bodyScrollWidth: body.scrollWidth, clientWidth: vw, wide: [] };
  const sel = 'button, h1, h2, p, label, input, a, .card, .modal-card, .slt-verify-card, img';
  document.querySelectorAll(sel).forEach((el) => {
    const cs = getComputedStyle(el);
    if (cs.display === 'none' || cs.visibility === 'hidden') return;
    if (el.closest('.hidden, [hidden]')) return;
    const r = el.getBoundingClientRect();
    if (r.width === 0 || r.height === 0) return;
    // Elements of screens that are not the active one are not on screen.
    const screen = el.closest('.screen');
    if (screen && !screen.classList.contains('active')) return;
    if (r.right > vw + 1 || r.left < -1) {
      out.wide.push((el.id ? '#' + el.id : el.tagName.toLowerCase() + '.' + String(el.className).split(' ')[0]) +
        ' left=' + Math.round(r.left) + ' right=' + Math.round(r.right));
    }
  });
  return out;
}
"""


def measure_overflow(page):
    return page.evaluate(OVERFLOW_JS)


def assert_no_overflow(page, label):
    m = measure_overflow(page)
    problems = []
    if m["scrollWidth"] > m["clientWidth"]:
        problems.append(f"document scrollWidth {m['scrollWidth']} > clientWidth {m['clientWidth']}")
    if m["bodyScrollWidth"] > m["clientWidth"]:
        problems.append(f"body scrollWidth {m['bodyScrollWidth']} > clientWidth {m['clientWidth']}")
    if m["wide"]:
        problems.append("elements outside the viewport: " + "; ".join(m["wide"][:6]))
    assert not problems, f"{label}: " + " | ".join(problems)
