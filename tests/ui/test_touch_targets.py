"""Touch rule for the voice screen: every button is at least 44 by 44 CSS px,
no button overlaps another or the logo, and nothing is outside the viewport.

The size and logo overlap checks are marked xfail (not strict) because they
describe what the new dashboard work is meant to fix (docs/UI_PLAN.md
section 5); they start passing by themselves once it lands.
"""

import pytest

from ui_helpers import open_screen, shot

VIEWPORTS = [(360, 740), (390, 844), (740, 360)]  # the last one is landscape
IDS = ["360x740", "390x844", "740x360-landscape"]

MIN_SIZE = 44

COLLECT_JS = """
(opts) => {
  const out = [];
  const seen = new Set();
  const root = opts.root ? document.querySelector(opts.root) : document;
  const nodes = [...root.querySelectorAll('button, [role="button"], a[href]')];
  if (!opts.root) {
    const extra = document.querySelector('#theme-toggle');
    if (extra && !nodes.includes(extra)) nodes.push(extra);
  }
  for (const el of nodes) {
    if (seen.has(el)) continue;
    seen.add(el);
    const cs = getComputedStyle(el);
    if (cs.display === 'none' || cs.visibility === 'hidden' || parseFloat(cs.opacity) === 0) continue;
    if (el.closest('.hidden, [hidden], [inert]')) continue;
    const screen = el.closest('.screen');
    if (screen && !screen.classList.contains('active')) continue;
    const r = el.getBoundingClientRect();
    if (r.width === 0 || r.height === 0) continue;
    if (opts.hitTest) {
      const hit = document.elementFromPoint(r.left + r.width / 2, r.top + r.height / 2);
      if (!hit || !(hit === el || el.contains(hit) || hit.contains(el))) continue;
    }
    out.push({
      label: el.getAttribute('aria-label') || el.id || (el.textContent || '').trim().slice(0, 24) || el.className,
      x: r.left, y: r.top, w: r.width, h: r.height,
    });
  }
  return out;
}
"""

LOGO_JS = """
() => {
  const img = document.querySelector('#screen-voice .brand-logo, #screen-voice .voice-logo img');
  if (!img) return null;
  const r = img.getBoundingClientRect();
  return { x: r.left, y: r.top, w: r.width, h: r.height };
}
"""

# Candidate triggers for the More sheet of the new dashboard.
MORE_TRIGGER_JS = """
() => {
  const screen = document.querySelector('#screen-voice');
  if (!screen) return null;
  const cands = [...document.querySelectorAll('button, [role="button"]')].filter((b) => {
    const text = ((b.getAttribute('aria-label') || '') + ' ' + (b.id || '') + ' ' + (b.className || '') + ' ' + (b.textContent || '')).toLowerCase();
    return /\\bmore\\b|dash-more|ui-more|ellipsis/.test(text) && b.offsetParent !== null;
  });
  if (!cands.length) return null;
  cands[0].setAttribute('data-qa-more', '1');
  return true;
}
"""


def _intersect(a, b):
    w = min(a["x"] + a["w"], b["x"] + b["w"]) - max(a["x"], b["x"])
    h = min(a["y"] + a["h"], b["y"] + b["h"]) - max(a["y"], b["y"])
    return w * h if w > 1 and h > 1 else 0


def _voice(make_page, base_url, width, height):
    page = make_page(width, height, "light", touch=True)
    open_screen(page, base_url, "voice")
    page.wait_for_timeout(600)
    return page


def _buttons(page, **opts):
    return page.evaluate(COLLECT_JS, {"root": None, "hitTest": False, **opts})


@pytest.mark.xfail(strict=False, reason="voice screen controls are under 44 px today; the dashboard work fixes this")
@pytest.mark.parametrize("size", VIEWPORTS, ids=IDS)
def test_buttons_at_least_44(make_page, base_url, size):
    page = _voice(make_page, base_url, *size)
    shot(page, f"touch-voice-{size[0]}x{size[1]}")
    small = [
        f"{b['label']} {b['w']:.0f}x{b['h']:.0f}"
        for b in _buttons(page)
        if b["w"] < MIN_SIZE - 0.5 or b["h"] < MIN_SIZE - 0.5
    ]
    assert not small, "buttons under 44x44: " + "; ".join(small)


@pytest.mark.parametrize("size", VIEWPORTS, ids=IDS)
def test_buttons_do_not_overlap_each_other(make_page, base_url, size):
    page = _voice(make_page, base_url, *size)
    items = _buttons(page)
    assert items, "no buttons found on the voice screen"
    clashes = []
    for i, a in enumerate(items):
        for b in items[i + 1 :]:
            area = _intersect(a, b)
            if area:
                clashes.append(f"{a['label']} / {b['label']} ({area:.0f} px2)")
    assert not clashes, "overlapping buttons: " + "; ".join(clashes)


@pytest.mark.xfail(strict=False, reason="the gender, logout and theme buttons sit on the logo today; the dashboard work fixes this")
@pytest.mark.parametrize("size", VIEWPORTS, ids=IDS)
def test_buttons_do_not_overlap_logo(make_page, base_url, size):
    page = _voice(make_page, base_url, *size)
    logo = page.evaluate(LOGO_JS)
    assert logo, "logo not found on the voice screen"
    clashes = [
        f"{b['label']} ({_intersect(b, logo):.0f} px2)" for b in _buttons(page) if _intersect(b, logo)
    ]
    assert not clashes, "buttons on the logo: " + "; ".join(clashes)


@pytest.mark.parametrize("size", VIEWPORTS, ids=IDS)
def test_buttons_inside_viewport(make_page, base_url, size):
    page = _voice(make_page, base_url, *size)
    width, height = size
    outside = [
        f"{b['label']} x={b['x']:.0f} y={b['y']:.0f} w={b['w']:.0f} h={b['h']:.0f}"
        for b in _buttons(page)
        if b["x"] < -0.5 or b["y"] < -0.5 or b["x"] + b["w"] > width + 0.5 or b["y"] + b["h"] > height + 0.5
    ]
    assert not outside, f"buttons outside the {width}x{height} viewport: " + "; ".join(outside)


@pytest.mark.parametrize("size", VIEWPORTS, ids=IDS)
def test_more_sheet_items(make_page, base_url, size):
    """After opening the More sheet every item is 44 by 44 or larger, none
    overlaps another and none is outside the viewport. Skips until the
    sheet exists."""
    page = _voice(make_page, base_url, *size)
    if not page.evaluate("() => document.documentElement.classList.contains('ui-on')"):
        pytest.skip("the new dashboard is not integrated yet (no ui-on class, no More sheet)")
    if not page.evaluate(MORE_TRIGGER_JS):
        pytest.skip("no More button found on the voice screen")
    page.click('[data-qa-more="1"]')
    page.wait_for_timeout(500)  # sheet slide in
    shot(page, f"touch-more-{size[0]}x{size[1]}")
    items = _buttons(page, hitTest=True)
    assert len(items) >= 4, f"expected the sheet and bar buttons, found {[i['label'] for i in items]}"
    width, height = size
    problems = []
    for b in items:
        if b["w"] < MIN_SIZE - 0.5 or b["h"] < MIN_SIZE - 0.5:
            problems.append(f"{b['label']} is {b['w']:.0f}x{b['h']:.0f}")
        if b["x"] < -0.5 or b["y"] < -0.5 or b["x"] + b["w"] > width + 0.5 or b["y"] + b["h"] > height + 0.5:
            problems.append(f"{b['label']} outside the viewport")
    for i, a in enumerate(items):
        for b in items[i + 1 :]:
            if _intersect(a, b):
                problems.append(f"{a['label']} overlaps {b['label']}")
    assert not problems, "; ".join(problems)
