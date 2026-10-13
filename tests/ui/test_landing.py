"""The landing overlay (docs/UI_PLAN.md section 3).

Detection: every test except the blocked scripts one skips until
window.SltUi.landing.run exists on the served page. The overlay itself is
found as a large fixed or absolute element whose id or class contains the
word landing, so the tests do not depend on its exact class names.
"""

import pytest

from ui_helpers import LANDING_JS, has_landing, landing_visible, ui_on

LANDING_FN = "(" + LANDING_JS.strip() + ")"
MAX_TOTAL_MS = 5600  # the first visit takes about 5.3 s (docs/UI_PLAN.md section 3)
MIN_TOTAL_MS = 4000  # and it must stay clearly longer than the old 2.5 s landing
SHORT_MS = 2000  # about 1.7 s on later visits; a little slack for a slow machine


def _load(page, base_url):
    page.goto(base_url + "/", wait_until="domcontentloaded")
    page.wait_for_function("() => document.readyState !== 'loading'")


def _need_landing(page):
    if not has_landing(page):
        pytest.skip("SltUi.landing is not integrated yet (window.SltUi.landing.run is undefined)")


def _wait_visible(page, timeout=6000):
    page.wait_for_function("() => (" + LANDING_FN + ")()", timeout=timeout)


def _wait_gone(page, timeout=6000):
    page.wait_for_function("() => !(" + LANDING_FN + ")()", timeout=timeout)


def _landing_run(make_page, base_url, **kwargs):
    page = make_page(390, 844, "light", landing_probe=True, **kwargs)
    _load(page, base_url)
    _need_landing(page)
    return page


def _duration(page):
    _wait_visible(page)
    _wait_gone(page, timeout=8000)
    page.wait_for_function("() => window.__landing && window.__landing.end !== null", timeout=3000)
    info = page.evaluate("() => window.__landing")
    return info["end"] - info["start"]


def test_landing_shows_on_first_load(make_page, base_url):
    page = _landing_run(make_page, base_url)
    _wait_visible(page)
    assert landing_visible(page)


def test_landing_total_duration_in_range(make_page, base_url):
    page = _landing_run(make_page, base_url)
    took = _duration(page)
    assert MIN_TOTAL_MS <= took < MAX_TOTAL_MS, f"landing lasted {took:.0f} ms"


def test_landing_has_rings_typing_and_quote(make_page, base_url):
    page = _landing_run(make_page, base_url)
    _wait_visible(page)
    info = page.evaluate(
        """() => {
          const q = document.querySelector('.lnd-q .lnd-type');
          const names = [...document.querySelectorAll('.slt-landing *')]
            .flatMap(e => e.getAnimations().map(a => a.animationName)).filter(Boolean);
          return {
            ripples: document.querySelectorAll('.lnd-ripple').length,
            carets: document.querySelectorAll('.lnd-caret').length,
            quote: q ? q.textContent : '',
            typing: names.includes('lnd-type'),
            morph: !!document.querySelector('.lnd-blob animate'),
          };
        }"""
    )
    assert info["ripples"] >= 3, f"only {info['ripples']} glowing circles"
    assert info["carets"] >= 2, "greeting and quote should both type with a caret"
    assert "No.1" in info["quote"], f"quote text is {info['quote']!r}"
    assert info["typing"], "no typing animation found"
    assert info["morph"], "no morph animation on the shape"


def test_skip_by_click(make_page, base_url):
    page = _landing_run(make_page, base_url)
    _wait_visible(page)
    page.mouse.click(195, 420)
    _wait_gone(page, timeout=1500)


def test_skip_by_tap(make_page, base_url):
    page = _landing_run(make_page, base_url, touch=True)
    _wait_visible(page)
    page.touchscreen.tap(195, 420)
    _wait_gone(page, timeout=1500)


def test_skip_by_key(make_page, base_url):
    page = _landing_run(make_page, base_url)
    _wait_visible(page)
    page.keyboard.press("Space")
    _wait_gone(page, timeout=1500)


def test_shortened_on_second_load_in_same_session(make_page, base_url):
    page = _landing_run(make_page, base_url)
    first = _duration(page)
    page.reload(wait_until="domcontentloaded")
    page.evaluate("() => { window.__landing = window.__landing || {}; }")
    second = _duration(page)
    assert second < first, f"second load {second:.0f} ms not shorter than first {first:.0f} ms"
    assert second <= SHORT_MS, f"second load took {second:.0f} ms, plan says about 1.7 s"


def test_first_screen_interactive_right_after_skip(make_page, base_url):
    page = _landing_run(make_page, base_url)
    _wait_visible(page)
    page.keyboard.press("Enter")
    _wait_gone(page, timeout=1500)
    page.click('#screen-language [data-language="en"]', timeout=2000)
    page.wait_for_selector("#screen-select.active", timeout=3000)


def test_reduced_motion_has_no_long_animation(make_page, base_url):
    page = make_page(390, 844, "light", reduced_motion="reduce", landing_probe=True)
    _load(page, base_url)
    _need_landing(page)
    long_ones = []
    for _ in range(8):
        found = page.evaluate(
            """() => {
              const out = [];
              const els = document.querySelectorAll('[class*="landing" i], [id*="landing" i]');
              for (const el of els) {
                for (const a of el.getAnimations({ subtree: true })) {
                  const t = a.effect && a.effect.getComputedTiming ? a.effect.getComputedTiming() : null;
                  if (!t) continue;
                  const total = t.endTime;
                  if (total === Infinity || total > 400) {
                    out.push((a.animationName || a.id || 'animation') + ' ' + (total === Infinity ? 'infinite' : Math.round(total) + 'ms'));
                  }
                }
              }
              return out;
            }"""
        )
        long_ones += found
        page.wait_for_timeout(120)
    assert not long_ones, "long animations under reduced motion: " + "; ".join(sorted(set(long_ones)))
    # With reduced motion the whole landing must also end quickly.
    if landing_visible(page):
        _wait_gone(page, timeout=3000)


def test_page_works_with_ui_scripts_blocked(make_page, base_url):
    """Runs on every frontend: with **/ui/** aborted the old screens still
    work, the root has no ui-on class and no landing is shown."""
    page = make_page(390, 844, "light", block_ui=True)
    page.goto(base_url + "/", wait_until="domcontentloaded")
    page.wait_for_selector("#screen-language.active", state="attached")
    page.wait_for_timeout(600)
    assert not landing_visible(page), "a landing showed although its scripts were blocked"
    assert not ui_on(page), "ui-on was set although the UI scripts were blocked"
    assert not page.evaluate("() => !!(window.SltUi && window.SltUi.ready)")
    page.click('#screen-language [data-language="en"]')
    page.wait_for_selector("#screen-select.active")
    page.click("#go-chat")
    page.wait_for_selector("#screen-user.active")
    page.click("#go-new")
    page.wait_for_selector("#screen-chat.active")
