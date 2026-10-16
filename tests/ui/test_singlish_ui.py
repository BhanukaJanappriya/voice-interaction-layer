"""Singlish typing in the chat box (frontend/singlish.js). Opt in like the
rest of tests/ui. Skips until the lead has added <script src="singlish.js">
to frontend/index.html (docs/patches/C_singlish.md).
"""

import pytest

from ui_helpers import open_screen


def test_chat_input_converts_in_sinhala_and_stays_latin_when_off(make_page, base_url):
    page = make_page(390, 844)
    open_screen(page, base_url, "chat", "si")
    if not page.evaluate("() => !!window.SltSinglish"):
        pytest.skip("frontend/singlish.js is not loaded yet (apply docs/patches/C_singlish.md)")

    page.click("#chat-input")
    page.keyboard.type("kohomada", delay=20)
    assert page.input_value("#chat-input") == "කොහොමද"

    # Backspace removes the whole cluster, not a single code point.
    page.keyboard.press("Backspace")
    assert page.input_value("#chat-input") == "කොහොම"

    # Toggle off: through the button when the patch added it, else the API.
    toggle = page.query_selector("#singlish-toggle")
    if toggle and toggle.is_visible():
        toggle.click()
    else:
        page.evaluate("() => window.SltSinglish.disable()")
    assert page.evaluate("() => window.SltSinglish.isEnabled()") is False
    page.fill("#chat-input", "")
    page.click("#chat-input")
    page.keyboard.type("kohomada", delay=20)
    assert page.input_value("#chat-input") == "kohomada"
