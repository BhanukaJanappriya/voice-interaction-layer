"""With prefers-reduced-motion: reduce, no long or endless animation runs on
a visible element (docs/UI_PLAN.md section 3: replaced by a 150 ms fade or
nothing). The check runs on the current frontend too, so a long animation
the old CSS keeps is reported as a finding."""

import pytest

from ui_helpers import HEIGHTS, open_screen

MAX_MS = 400

ANIMATIONS_JS = """
() => {
  const out = [];
  for (const a of document.getAnimations()) {
    const eff = a.effect;
    const target = eff && eff.target;
    if (!target || !target.getBoundingClientRect) continue;
    const r = target.getBoundingClientRect();
    const cs = getComputedStyle(target);
    if (r.width === 0 || r.height === 0 || cs.visibility === 'hidden' || cs.display === 'none') continue;
    if (a.playState !== 'running') continue;
    const t = eff.getComputedTiming();
    if (t.endTime === Infinity || t.endTime > %d) {
      const name = a.animationName || a.transitionProperty || a.id || 'animation';
      const who = target.id ? '#' + target.id : target.tagName.toLowerCase() + '.' + String(target.className).split(' ')[0];
      out.push(who + ' ' + name + ' ' + (t.endTime === Infinity ? 'infinite' : Math.round(t.endTime) + 'ms'));
    }
  }
  return [...new Set(out)];
}
""" % MAX_MS

SCREENS = ["language", "select", "voice", "wizard1", "mic"]


def test_reduced_motion_preference_is_honoured_by_the_browser(make_page, base_url):
    page = make_page(390, HEIGHTS[390], "light", reduced_motion="reduce")
    page.goto(base_url + "/", wait_until="domcontentloaded")
    assert page.evaluate("() => matchMedia('(prefers-reduced-motion: reduce)').matches")


@pytest.mark.parametrize("screen", SCREENS)
def test_no_long_animation_under_reduced_motion(make_page, base_url, screen):
    page = make_page(390, HEIGHTS[390], "light", reduced_motion="reduce")
    open_screen(page, base_url, screen)
    page.wait_for_timeout(700)
    found = page.evaluate(ANIMATIONS_JS)
    assert not found, f"{screen}: long or endless animations under reduced motion: {found}"
