Browser run of the verification wizard (Chrome, English, Sinhala, Tamil)

What this is
  wizard_e2e.md is a precise step list for one person, or for Claude driving
  Chrome through the claude-in-chrome tools, to walk the "Existing user"
  verification wizard in all three languages against a real local server.
  The automated side of the same rules lives in
  tests/test_e2e_verification.py (HTTP level). This run adds what only a
  browser can show: layout, fonts, focus, keyboard, language switching and
  the real cookie round trip.

Before you start
  1. Work from the repository root. Use the project venv:
       venv/Scripts/python.exe
  2. Apply the database migrations (revision 0003 must be part of them):
       cd backend
       ..\venv\Scripts\python.exe -m db.migrate
  3. Seed the FAKE demo customers (the only rows the wizard knows):
       ..\venv\Scripts\python.exe -m verification.seed_demo
     Numbers and names are in wizard_e2e.md, section "Test data".
  4. Start the server with the console sender, so the code appears in the
     server window instead of a phone:
       set OTP_SENDER=console
       set VERIFY_ENABLED=true
       set ACCESS_CONTROL_ENABLED=true
       ..\venv\Scripts\python.exe server.py
     Leave that window visible. Every code is printed there as
     "[OTP DEV ONLY ...] one time code for <masked>: 123456". This is the
     only place a code ever shows up.
  5. Open http://127.0.0.1:8000 in Chrome. Use a fresh profile or an
     incognito window so no old user_session cookie exists.

Rules for the run
  * Never use a real customer number. Only the seeded demo numbers.
  * Do not paste a code anywhere except the wizard.
  * Record each step as PASS or FAIL with one line of evidence (a
    screenshot name, a console line, a visible text).
  * A FAIL in Sinhala or Tamil that is only about wording is not a bug: the
    texts are machine written and flagged for native speaker review. A
    FAIL about layout, empty glyph boxes (missing font), wrong language, or
    a wrong behaviour is a bug.

Using Claude in Chrome
  Load the tools in one call: tabs_context_mcp, navigate, computer,
  read_page, find, form_input, read_console_messages,
  read_network_requests, resize_window, gif_creator. Open the app in a new
  tab. The browser tools cannot see the server window, so ask the person
  running the test to read each code out, or read the server output
  yourself if you started the server.

Result
  Fill the table at the end of wizard_e2e.md and keep the screenshots.
