"""Firefox and WebKit, only if Playwright already has them installed
(nothing is downloaded). Otherwise the test skips with the reason, which is
what the final report lists as not run."""

import pytest

from ui_helpers import HEIGHTS, assert_no_overflow, open_screen


@pytest.mark.parametrize("engine", ["firefox", "webkit"])
def test_language_and_voice_screens_in_other_engines(playwright_obj, make_page, base_url, engine):
    try:
        browser = getattr(playwright_obj, engine).launch(headless=True)
    except Exception as exc:
        pytest.skip(f"{engine} is not installed for Playwright (not downloaded here): {str(exc).splitlines()[0][:120]}")
    try:
        page = make_page(360, HEIGHTS[360], "light", browser=browser)
        for screen in ("language", "voice"):
            open_screen(page, base_url, screen)
            assert_no_overflow(page, f"{engine} {screen} 360px")
    finally:
        browser.close()
