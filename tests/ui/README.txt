Browser tests for the UI layer (Playwright for Python, system Chrome)

Run
  venv/Scripts/python.exe -m pytest tests/ui -q
  Parts:  -k layout | touch | landing | modals | a11y | reduced | dark
  Slow machine: the full run takes several minutes (about 200 browser contexts).
  There is no per test timeout plugin; every wait inside a test has its own
  timeout (20 s per action, 30 s for the real wizard), so a hang ends in a
  failure, not a stuck run.

What the fixtures do (tests/ui/conftest.py)
  * Starts the real app: python -m uvicorn server:app from backend/, on a free
    port (never 8000 or 8011 to 8014), with a throwaway DATA_DIR and sqlite
    DATABASE_URL, OTP_SENDER=console, VERIFY_OTP_SENDER=console,
    VOICE_TRANSPORT=websocket, WARM_UP_ON_STARTUP=false, COOKIE_SECURE=false,
    a test JWT_SECRET, empty LLM_CHAIN, empty MCP hub and cloud keys.
  * Migrates the scratch database and seeds the three FAKE directory rows
    (python -m verification.seed_demo). The dev console OTP is read from the
    server's stdout log file.
  * Launches Chrome with channel="chrome". No browser is downloaded. If
    playwright or Chrome is missing the whole folder skips with that reason.
  * Blocks cdn.jsdelivr.net and the Google fonts hosts (three.js, livekit,
    Noto fonts) so runs are fast and offline; the page has fallbacks for all.
    Sinhala and Tamil therefore render with the system fonts in these tests.

What skips, and why (until the lead integrates frontend/ui)
  test_landing.py       all but the "scripts blocked" test: window.SltUi.landing
                        is undefined. The overlay is found as a large fixed
                        element whose id or class contains "landing".
  test_modals.py        badge tests: window.SltUi.badge is undefined. Progress
                        test: no ui-on class on the root element.
  test_touch_targets    More sheet test: no ui-on class, or no button whose
                        label, id or class says "more".
  test_a11y.py          axe cases skip if axe_baseline.json has no entry for the
                        screen (re-record: UI_WRITE_AXE_BASELINE=1, commit).
  test_other_browsers   Firefox and WebKit skip when not installed for Playwright.
  Everything else runs on the current frontend too.

xfail (not strict): touch target size under 44 px and buttons on the logo on
the voice screen. They are what the dashboard work fixes and report XPASS
once it does. Remove the markers then.

Screenshots
  docs/ui-after/NAME-THEME-WIDTH.png (gitignored). test_layout writes every
  screen at 360, 390, 768, 1024 and 1440 px, light and dark, plus si and ta at
  360 px. Other tests add touch-*, focus-*, badge-* images.

Rate limits of the wizard (real backend test)
  One code per number per 30 s and 6 per number per hour. The end to end test
  uses a different seeded number per language and sends one code each. Screens
  that only need to look at the wizard use stubbed /verify answers instead.
