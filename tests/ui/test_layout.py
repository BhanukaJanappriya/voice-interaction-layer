"""Screenshots of every screen at five widths in both themes, and the rule
that nothing scrolls sideways at 360 px, also in Sinhala and Tamil."""

import pytest

from ui_helpers import (
    HEIGHTS,
    THEMES,
    WIDTHS,
    assert_no_overflow,
    open_screen,
    shot,
)

SCREENS = [
    "language",
    "select",
    "user",
    "wizard1",
    "wizard2",
    "wizard3",
    "mic",
    "voice",
    "history",
    "chat",
]

# Screens whose text is long enough to overflow in Sinhala or Tamil.
LONG_TEXT_SCREENS = ["language", "user", "wizard1", "wizard2", "wizard3", "voice"]


@pytest.mark.parametrize("width", WIDTHS)
@pytest.mark.parametrize("theme", THEMES)
def test_screenshots_every_screen(make_page, base_url, theme, width):
    """Takes the screenshots (docs/ui-after/NAME-THEME-WIDTH.png). A screen
    that cannot be reached is a failure, collected for all screens."""
    page = make_page(width, HEIGHTS[width], theme)
    failures = []
    for name in SCREENS:
        try:
            open_screen(page, base_url, name)
            page.wait_for_timeout(450)  # let the screen and modal animations settle
            shot(page, name)
        except Exception as exc:
            failures.append(f"{name}: {str(exc).splitlines()[0][:140]}")
    assert not failures, "screens not reachable: " + " | ".join(failures)


@pytest.mark.parametrize("theme", THEMES)
@pytest.mark.parametrize("screen", SCREENS)
def test_no_horizontal_scroll_360(make_page, base_url, screen, theme):
    page = make_page(360, HEIGHTS[360], theme)
    open_screen(page, base_url, screen)
    page.wait_for_timeout(400)
    assert_no_overflow(page, f"{screen} {theme} 360px")


@pytest.mark.parametrize("lang", ["si", "ta"])
@pytest.mark.parametrize("screen", LONG_TEXT_SCREENS)
def test_no_overflow_si_ta_360(make_page, base_url, screen, lang):
    page = make_page(360, HEIGHTS[360], "light")
    open_screen(page, base_url, screen, lang=lang)
    page.wait_for_timeout(400)
    shot(page, f"{screen}-{lang}")
    assert_no_overflow(page, f"{screen} {lang} 360px")
