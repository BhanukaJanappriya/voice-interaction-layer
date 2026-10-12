"""Accessibility: axe on every screen at 360 px in both themes, compared with
a recorded baseline, plus visible focus and names on icon buttons.

Baseline: tests/ui/axe_baseline.json maps "SCREEN|THEME" to
{rule id: number of failing nodes} for serious and critical violations,
colour contrast included. A run fails when a rule appears that is not in the
baseline or has more failing nodes than recorded. To re-record after a
reviewed change, run once with UI_WRITE_AXE_BASELINE=1 (the file is rewritten
from that run; commit it).

The known baseline issue is landmark-one-main (no main landmark on most
screens); the lead's wrapper is meant to remove it, and a rule that
disappears is simply an improvement (it never fails).
"""

import json
import os
from pathlib import Path

import pytest

from ui_helpers import HEIGHTS, THEMES, open_screen, shot

axe_mod = pytest.importorskip("axe_playwright_python.sync_playwright", reason="axe-playwright-python is not installed")

BASELINE_PATH = Path(__file__).resolve().parent / "axe_baseline.json"
WRITE = os.environ.get("UI_WRITE_AXE_BASELINE") == "1"

SCREENS = ["language", "select", "user", "wizard1", "wizard2", "wizard3", "mic", "voice", "history", "chat"]


def _load_baseline():
    try:
        return json.loads(BASELINE_PATH.read_text("utf-8"))
    except (OSError, ValueError):
        return {}


def _record(key, counts):
    data = _load_baseline()
    data[key] = counts
    BASELINE_PATH.write_text(json.dumps(dict(sorted(data.items())), indent=2) + "\n", "utf-8")


@pytest.mark.parametrize("theme", THEMES)
@pytest.mark.parametrize("screen", SCREENS)
def test_axe_no_new_serious_violations(make_page, base_url, screen, theme):
    # reduced motion keeps axe from measuring colours in the middle of a fade
    page = make_page(360, HEIGHTS[360], theme, reduced_motion="reduce")
    open_screen(page, base_url, screen)
    page.wait_for_timeout(500)
    results = axe_mod.Axe().run(page).response
    counts = {}
    detail = {}
    for v in results.get("violations", []):
        if v.get("impact") in ("serious", "critical"):
            counts[v["id"]] = len(v.get("nodes", []))
            detail[v["id"]] = [str(n["target"][0]) for n in v["nodes"][:3] if n.get("target")]
    key = f"{screen}|{theme}"
    if WRITE:
        _record(key, counts)
        return
    baseline = _load_baseline()
    if key not in baseline:
        pytest.skip(f"no recorded axe baseline for {key}; run with UI_WRITE_AXE_BASELINE=1 once")
    known = baseline[key]
    new = {
        rule: (count, detail.get(rule))
        for rule, count in counts.items()
        if count > known.get(rule, 0)
    }
    assert not new, f"new serious or critical axe violations on {key}: {new}"


def test_axe_contrast_rule_is_enabled(make_page, base_url):
    """Guards the test itself: color-contrast must be one of the rules axe
    runs, otherwise the baseline comparison would silently ignore it."""
    page = make_page(360, HEIGHTS[360], "light", reduced_motion="reduce")
    open_screen(page, base_url, "language")
    results = axe_mod.Axe().run(page, options={"resultTypes": ["violations", "passes", "incomplete"]}).response
    ids = {r["id"] for key in ("violations", "passes", "incomplete") for r in results.get(key, [])}
    assert "color-contrast" in ids


FOCUS_JS = """
() => {
  const el = document.activeElement;
  if (!el || el === document.body) return null;
  const cs = getComputedStyle(el);
  const outline = cs.outlineStyle !== 'none' && parseFloat(cs.outlineWidth) > 0;
  const shadow = cs.boxShadow && cs.boxShadow !== 'none';
  const ring = el.closest('label') ? getComputedStyle(el.closest('label')).outlineStyle !== 'none' : false;
  const r = el.getBoundingClientRect();
  return { label: el.getAttribute('aria-label') || el.id || (el.textContent || '').trim().slice(0, 20) || el.tagName,
           visible: outline || shadow || ring, size: [r.width, r.height] };
}
"""


@pytest.mark.parametrize("screen", ["language", "select", "user", "voice", "chat"])
def test_keyboard_focus_is_visible(make_page, base_url, screen):
    page = make_page(1024, HEIGHTS[1024], "light", reduced_motion="reduce")
    open_screen(page, base_url, screen)
    page.wait_for_timeout(300)
    hidden_focus = []
    for _ in range(8):
        page.keyboard.press("Tab")
        info = page.evaluate(FOCUS_JS)
        if info and info["size"][0] > 0 and not info["visible"]:
            hidden_focus.append(info["label"])
    shot(page, f"focus-{screen}")
    assert not hidden_focus, f"{screen}: focused without a visible indicator: {hidden_focus}"


UNNAMED_JS = """
() => {
  const out = [];
  document.querySelectorAll('button, [role="button"], a[href]').forEach((el) => {
    const cs = getComputedStyle(el);
    if (cs.display === 'none' || cs.visibility === 'hidden') return;
    if (el.closest('.hidden, [hidden]')) return;
    const screen = el.closest('.screen');
    if (screen && !screen.classList.contains('active')) return;
    const r = el.getBoundingClientRect();
    if (r.width === 0 || r.height === 0) return;
    const text = (el.innerText || '').trim();
    const named = text || el.getAttribute('aria-label') || el.getAttribute('aria-labelledby') || el.getAttribute('title');
    if (!named) out.push(el.id || el.className || el.tagName);
  });
  return out;
}
"""


@pytest.mark.parametrize("screen", SCREENS)
def test_icon_buttons_have_accessible_names(make_page, base_url, screen):
    page = make_page(360, HEIGHTS[360], "light", reduced_motion="reduce")
    open_screen(page, base_url, screen)
    page.wait_for_timeout(300)
    unnamed = page.evaluate(UNNAMED_JS)
    assert not unnamed, f"{screen}: buttons without a text or aria-label name: {unnamed}"


@pytest.mark.xfail(strict=False, reason="known baseline issue: the screens have no main landmark; the UI wrapper fixes it")
@pytest.mark.parametrize("screen", ["language", "select", "user", "voice"])
def test_page_has_one_main_landmark(make_page, base_url, screen):
    page = make_page(360, HEIGHTS[360], "light", reduced_motion="reduce")
    open_screen(page, base_url, screen)
    count = page.evaluate(
        "() => [...document.querySelectorAll('main, [role=main]')].filter(m => m.offsetParent !== null || m.getClientRects().length).length"
    )
    assert count == 1, f"{screen}: {count} visible main landmarks"
