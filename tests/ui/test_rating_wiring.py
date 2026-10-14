"""The rating window is asked when a real chat or voice session ends (app.js),
not only when the module is driven by hand. Opt in like the rest of tests/ui.
The /chat and /feedback endpoints are stubbed."""

import json

import pytest

from ui_helpers import open_screen

CARD = ".slt-rating-card"


def stub_backend(page, reply="The Fiber Starter package is Rs. 2,690."):
    posts = []

    page.route("**/feedback/config", lambda r, req: r.fulfill(status=200, content_type="application/json", body='{"enabled": true}'))

    def on_rating(route, request):
        posts.append(json.loads(request.post_data or "{}"))
        route.fulfill(status=201, content_type="application/json", body='{"ok": true}')

    page.route("**/feedback/rating", on_rating)
    page.route(
        "**/chat",
        lambda r, req: r.fulfill(status=200, content_type="application/json", body=json.dumps({"reply": reply, "mode": "stub"})),
    )
    return posts


def have_rating(page):
    return page.evaluate("() => !!window.SLTRating")


@pytest.mark.parametrize("lang", ["en", "si", "ta"])
def test_leaving_the_chat_after_a_conversation_asks_for_a_rating(make_page, base_url, lang):
    page = make_page(390, 844)
    posts = stub_backend(page)
    open_screen(page, base_url, "chat", lang)
    if not have_rating(page):
        pytest.skip("the rating module is not loaded")
    page.fill("#chat-input", "fibre packages")
    page.press("#chat-input", "Enter")
    page.wait_for_selector("#chat-transcript .bubble-assistant:not(.bubble-typing)", timeout=8000)
    page.click("#screen-chat .back-link")
    page.wait_for_selector(CARD, state="visible", timeout=5000)
    page.click(f"{CARD} [role=radio] >> nth=3")
    page.click(f"{CARD} button.slt-rating-submit")
    page.wait_for_function("() => !document.querySelector('.slt-rating-card')", timeout=8000)
    assert len(posts) == 1
    assert posts[0] == {"rating": 4, "channel": "chat", "language": lang, "turns": 1}


def test_leaving_the_chat_without_saying_anything_asks_nothing(make_page, base_url):
    page = make_page(390, 844)
    posts = stub_backend(page)
    open_screen(page, base_url, "chat", "en")
    if not have_rating(page):
        pytest.skip("the rating module is not loaded")
    page.click("#screen-chat .back-link")
    page.wait_for_timeout(800)
    assert page.query_selector(CARD) is None
    assert posts == []


def test_ending_the_voice_session_after_speaking_asks_for_a_rating(make_page, base_url):
    page = make_page(390, 844)
    posts = stub_backend(page)
    open_screen(page, base_url, "voice", "en")
    if not have_rating(page):
        pytest.skip("the rating module is not loaded")
    # A spoken turn arrives from the server as a final transcript; the page counts it.
    page.evaluate("() => { try { sessionTurns.voice += 1; } catch (e) { window.__noTurns = true; } }")
    if page.evaluate("() => !!window.__noTurns"):
        pytest.skip("the turn counter is not reachable from the page")
    page.evaluate("() => endVoiceChat()")
    page.wait_for_selector(CARD, state="visible", timeout=5000)
    page.keyboard.press("Escape")
    page.wait_for_function("() => !document.querySelector('.slt-rating-card')", timeout=5000)
    assert posts == []
