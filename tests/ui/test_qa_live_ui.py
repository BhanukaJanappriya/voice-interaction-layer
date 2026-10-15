"""QA browser tests for the Gemini Live tier in the page (opt in, like the rest of tests/ui).

Run in small slices, one test at a time:

    pytest tests/ui/test_qa_live_ui.py -k "test_live_transcript_shows_the_badge and en"

The page talks to the real app served by the app_server fixture, but three things
are replaced inside the browser:

  * GET /api/voice/tier is answered by the test (page.route),
  * /ws/live and /ws/voice are fake WebSockets (page.route_web_socket) that record
    what the page sends and say what the test scripts,
  * navigator.mediaDevices.getUserMedia returns a silent synthetic stream, so no
    microphone or browser flag is needed.

Nothing reaches Gemini, LiveKit or any other network.
"""

import json
import time

import pytest

from ui_helpers import REPO_ROOT, open_screen

I18N_DIR = REPO_ROOT / "frontend" / "i18n"
LANGS = ["en", "si", "ta"]
MODEL = "gemini-3.8-live"

FAKE_MIC = """
(() => {
  const md = navigator.mediaDevices;
  if (!md) return;
  md.getUserMedia = async function () {
    const ctx = new (window.AudioContext || window.webkitAudioContext)();
    const osc = ctx.createOscillator();
    const gain = ctx.createGain();
    gain.gain.value = 0;
    const dest = ctx.createMediaStreamDestination();
    osc.connect(gain); gain.connect(dest); osc.start();
    return dest.stream;
  };
})();
"""


def label(lang, key, **values):
    text = json.loads((I18N_DIR / f"{lang}.json").read_text(encoding="utf-8"))[key]
    for name, value in values.items():
        text = text.replace("{" + name + "}", str(value))
    return text


class Fakes:
    """What the page opened and sent, and what the test lets the fake servers say."""

    def __init__(self):
        self.tier = {"tier": "gemini_live", "model": MODEL}
        self.tier_requests = 0
        self.live_starts = []
        self.live_sockets = 0
        self.voice_starts = []
        self.voice_sockets = 0
        self.live_script = None  # called with (server_ws, start_dict) when the page starts a live session

    def wait_for(self, condition, seconds=15.0):
        end = time.time() + seconds
        while time.time() < end:
            if condition():
                return True
            self.page.wait_for_timeout(100)
        return False


@pytest.fixture
def fakes(make_page, base_url):
    page = make_page(width=1024, height=768)
    page.add_init_script(FAKE_MIC)
    state = Fakes()
    state.page = page
    state.base_url = base_url

    def tier_handler(route):
        state.tier_requests += 1
        route.fulfill(status=200, content_type="application/json", body=json.dumps(state.tier))

    page.route("**/api/voice/tier", tier_handler)

    def live_socket(ws):
        state.live_sockets += 1

        def on_message(message):
            if isinstance(message, str):
                try:
                    data = json.loads(message)
                except ValueError:
                    return
                if data.get("type") == "start_session":
                    state.live_starts.append(data)
                    if state.live_script:
                        state.live_script(ws, data)

        ws.on_message(on_message)

    def voice_socket(ws):
        state.voice_sockets += 1

        def on_message(message):
            if isinstance(message, str):
                try:
                    data = json.loads(message)
                except ValueError:
                    return
                if data.get("type") == "start_session":
                    state.voice_starts.append(data)

        ws.on_message(on_message)

    page.route_web_socket("**/ws/live", live_socket)
    page.route_web_socket("**/ws/voice", voice_socket)
    return state


def say(ws, **payload):
    ws.send(json.dumps(payload, ensure_ascii=False))


def start_voice(fakes, lang="en", voice_style=None, token=None):
    page = fakes.page
    if voice_style == "male":
        page.add_init_script("try { localStorage.setItem('avatarGender', 'male'); } catch (e) {}")
    open_screen(page, fakes.base_url, "mic", lang)
    if token:
        page.evaluate("(t) => { sessionToken = t; }", token)
    page.click("#allow-mic-button")


def script_a_turn(then_fail=False):
    def script(ws, start):
        say(ws, type="live_ready")
        say(ws, type="status", state="listening")
        say(ws, type="final_transcript", text="what is the price of fibre", language=start.get("language_hint"))
        say(ws, type="agent_response", text="Fibre starts at four thousand rupees.", mode="gemini live")
        ws.send(b"\x00\x01" * 2400)
        say(ws, type="speaking_done")
        if then_fail:
            say(ws, type="live_failed", reason="billing_or_quota")

    return script


# Transcript and badge


@pytest.mark.parametrize("lang", LANGS)
def test_live_transcript_shows_the_badge(fakes, lang):
    fakes.live_script = script_a_turn()
    start_voice(fakes, lang)
    page = fakes.page
    page.wait_for_selector("#transcript .bubble-assistant .bubble-meta", state="attached", timeout=20000)
    meta = page.inner_text("#transcript .bubble-assistant .bubble-meta") if page.is_visible(
        "#transcript .bubble-assistant .bubble-meta"
    ) else page.text_content("#transcript .bubble-assistant .bubble-meta")
    assert meta.strip() == label(lang, "voice.mode.gemini_live", model=MODEL)
    assert meta.strip() == f"Gemini Live ({MODEL})" or lang != "en"
    user = page.text_content("#transcript .bubble-user")
    assert "what is the price of fibre" in user
    assistant = page.text_content("#transcript .bubble-assistant")
    assert "Fibre starts at four thousand rupees." in assistant
    # the page started the live socket with this language and the default voice
    assert fakes.live_starts[0]["language_hint"] == lang
    assert fakes.live_starts[0]["voice_style"] == "female_natural"
    assert fakes.voice_sockets == 0, "tier 1 must not also open /ws/voice"


def test_the_localized_badge_text_exists_for_si_and_ta_and_is_not_a_missing_key():
    for lang in ("si", "ta"):
        text = label(lang, "voice.mode.gemini_live", model=MODEL)
        assert "{" not in text and "voice.mode" not in text and MODEL in text


def test_live_start_message_carries_the_chosen_voice_and_never_a_key(fakes):
    fakes.live_script = script_a_turn()
    start_voice(fakes, "ta", voice_style="male", token="qa-session-token-1")
    assert fakes.wait_for(lambda: fakes.live_starts)
    start = fakes.live_starts[0]
    assert start["voice_style"] == "male" and start["language_hint"] == "ta"
    assert start["session_token"] == "qa-session-token-1"
    assert "api_key" not in json.dumps(start).lower()


# Failure after ready: the notice and the next tier


@pytest.mark.parametrize("lang", LANGS)
def test_live_failed_after_ready_shows_the_notice_and_starts_the_next_tier(fakes, lang):
    def switch_tier(ws, start):
        # The server reports the failure to the router before the page asks again.
        fakes.tier = {"tier": "local", "model": MODEL}
        script_a_turn(then_fail=True)(ws, start)

    fakes.live_script = switch_tier
    start_voice(fakes, lang, voice_style="male", token="qa-session-token-2")
    page = fakes.page
    assert fakes.wait_for(lambda: fakes.voice_starts, 20), "the next tier never started"
    nxt = fakes.voice_starts[0]
    assert nxt["handover"] is True
    assert nxt["language_hint"] == lang
    assert nxt["voice_style"] == "male"
    assert nxt["session_token"] == "qa-session-token-2"
    assert nxt["greet"] is False
    assert fakes.live_sockets == 1, "the failed tier must not be retried"
    assert fakes.tier_requests == 1, "after a live failure the next tier starts without asking the router again"


@pytest.mark.parametrize("lang", LANGS)
def test_the_reconnecting_notice_is_shown_while_the_page_switches(fakes, lang):
    """The caption holds the localized notice during the 300 ms pause before the next tier starts."""
    page = fakes.page

    def failing(ws, start):
        fakes.tier = {"tier": "local", "model": MODEL}
        say(ws, type="live_ready")
        say(ws, type="live_failed", reason="outage")

    fakes.live_script = failing
    # slow the next tier's decision down (the page now skips the tier request) so the caption can be observed
    def slow_config(route):
        time.sleep(1.5)
        route.continue_()

    page.route("**/api/livekit/config", slow_config)
    start_voice(fakes, lang)
    notice = label(lang, "voice.reconnecting")
    page.wait_for_function(
        "(n) => (document.getElementById('captions-text') || {}).textContent === n", arg=notice, timeout=15000
    )
    assert fakes.wait_for(lambda: fakes.voice_starts, 20)
    assert fakes.voice_starts[0]["handover"] is True


def test_a_live_session_that_never_gets_ready_falls_through_without_a_notice_handover(fakes):
    """A failure before live_ready is a plain fall through (handover stays as the caller gave it)."""

    def never_ready(ws, start):
        say(ws, type="live_failed", reason="missing_key")

    fakes.live_script = never_ready
    start_voice(fakes, "en")
    assert fakes.wait_for(lambda: fakes.voice_starts, 20)
    assert fakes.voice_starts[0].get("handover") is False
    assert fakes.voice_starts[0]["greet"] is True


# The router decides: no live socket on other tiers


@pytest.mark.parametrize("tier", ["local", "livekit"])
def test_the_live_socket_is_never_opened_when_the_tier_is_not_gemini_live(fakes, tier):
    fakes.tier = {"tier": tier}
    start_voice(fakes, "en")
    assert fakes.wait_for(lambda: fakes.voice_starts, 20), "the page never fell through to /ws/voice"
    assert fakes.live_sockets == 0
    assert fakes.live_starts == []
    assert fakes.tier_requests >= 1


def test_the_live_socket_is_never_opened_when_the_tier_request_fails(fakes):
    page = fakes.page
    page.unroute("**/api/voice/tier")
    page.route("**/api/voice/tier", lambda route: route.fulfill(status=500, body="boom"))
    start_voice(fakes, "en")
    assert fakes.wait_for(lambda: fakes.voice_starts, 20)
    assert fakes.live_sockets == 0


# Gender toggle during a live call


def test_toggling_gender_during_a_live_call_opens_a_new_live_socket_with_the_other_voice(fakes):
    def ready_only(ws, start):
        say(ws, type="live_ready")
        say(ws, type="status", state="listening")

    fakes.live_script = ready_only
    start_voice(fakes, "en")
    assert fakes.wait_for(lambda: len(fakes.live_starts) == 1, 20)
    assert fakes.live_starts[0]["voice_style"] == "female_natural"
    fakes.page.click("#gender-toggle")
    assert fakes.wait_for(lambda: len(fakes.live_starts) == 2, 20), "no new live session after the toggle"
    second = fakes.live_starts[1]
    assert second["voice_style"] == "male" and second["language_hint"] == "en"
    assert second["greet"] is False
    assert fakes.voice_sockets == 0


# Lip sync


@pytest.mark.skip(
    reason="Not feasible in headless Chrome here: the offset between the PCM the page plays and the "
    "mouth blend shapes needs the WebGL avatar rendered with real frame timing (the three.js and "
    "TalkingHead modules come from a blocked CDN in these tests, and software GL gives irregular "
    "frames), plus a measured reference such as docs/ARCHITECTURE.md 'Audio to mouth offset' captured "
    "on a real GPU. To test it: serve the vendor files locally, play a known test tone burst through "
    "LiveVoice, sample window.avatarBridge morph target 'jawOpen' each requestAnimationFrame and "
    "compare the first rise with the AudioContext time of the burst; run it with a GPU headed browser."
)
def test_lip_sync_offset_on_gemini_audio():
    pass
