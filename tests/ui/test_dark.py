"""Dark theme: the stored choice and the operating system preference both
give a dark page, the toggle switches it, and no card or modal stays
light on a dark page."""

import pytest

from ui_helpers import HEIGHTS, open_screen

LUMINANCE_JS = """
(sel) => {
  const el = document.querySelector(sel);
  if (!el) return null;
  const parse = (c) => (c.match(/[\\d.]+/g) || []).map(Number);
  let node = el, rgba = [0, 0, 0, 0];
  while (node) {
    rgba = parse(getComputedStyle(node).backgroundColor);
    if (rgba.length >= 3 && (rgba.length < 4 || rgba[3] > 0.5)) break;
    node = node.parentElement;
  }
  const [r, g, b] = rgba.length >= 3 ? rgba : [255, 255, 255];
  return 0.2126 * r / 255 + 0.7152 * g / 255 + 0.0722 * b / 255;
}
"""

SURFACES = {
    "language": ["body", "#screen-language .card"],
    "select": ["#screen-select .card"],
    "user": ["#screen-user .card"],
    "mic": ["#mic-permission-modal .modal-card"],
    "history": ["#history-panel .modal-card"],
    "chat": ["#screen-chat .card"],
    "wizard1": [".slt-verify-card"],
}


@pytest.mark.parametrize("screen", list(SURFACES))
def test_surfaces_are_dark_in_dark_theme(make_page, base_url, screen):
    page = make_page(390, HEIGHTS[390], "dark", reduced_motion="reduce")
    open_screen(page, base_url, screen)
    page.wait_for_timeout(300)
    light = []
    for selector in SURFACES[screen]:
        lum = page.evaluate(LUMINANCE_JS, selector)
        assert lum is not None, f"{selector} not found on {screen}"
        if lum > 0.45:
            light.append(f"{selector} luminance {lum:.2f}")
    assert not light, f"{screen}: light surfaces in dark theme: {light}"


def test_os_dark_preference_without_stored_theme(make_page, base_url):
    page = make_page(390, HEIGHTS[390], "dark", reduced_motion="reduce", store_theme=False)
    page.goto(base_url + "/", wait_until="domcontentloaded")
    page.wait_for_selector("#screen-language.active", state="attached")
    page.wait_for_timeout(300)
    assert page.evaluate(LUMINANCE_JS, "body") < 0.45


def test_theme_toggle_switches_theme(make_page, base_url):
    page = make_page(390, HEIGHTS[390], "light", reduced_motion="reduce")
    page.goto(base_url + "/", wait_until="domcontentloaded")
    page.wait_for_selector("#screen-language.active", state="attached")
    page.wait_for_timeout(300)
    before = page.evaluate(LUMINANCE_JS, "body")
    page.click("#theme-toggle")
    page.wait_for_timeout(600)  # the theme fade
    after = page.evaluate(LUMINANCE_JS, "body")
    assert abs(before - after) > 0.3, f"theme toggle did not change the page (body luminance {before:.2f} then {after:.2f})"
    page.reload(wait_until="domcontentloaded")
    # The app stores the choice, so the init script that forces the starting
    # theme must not be what keeps the page dark; just check the page loads.
    page.wait_for_selector("#screen-language.active", state="attached")
