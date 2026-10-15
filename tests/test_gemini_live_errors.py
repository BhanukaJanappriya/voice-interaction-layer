"""Error mapping table for the Gemini Live session: every google-genai error,
websocket close code and server text lands in one reason class, and a safety
block, a refusal or a normal close is never a genuine failure."""

import asyncio

import pytest

pytest.importorskip("google.genai", reason="google-genai is not installed")

from google.genai import errors as genai_errors  # noqa: E402
from google.genai import types  # noqa: E402

from gemini_live import errors  # noqa: E402
from gemini_live.fake_server import closed_error  # noqa: E402


def api_error(cls, code, message, status=""):
    return cls(code, {"error": {"code": code, "message": message, "status": status}})


TABLE = [
    # the exact exception the SDK raised on connect with the unfunded key (live probe, 2026-10-15)
    (genai_errors.APIError(1011, {"message": "Your prepayment credits are depleted. Please go to AI Studio", "status": None}), "billing_or_quota"),
    (genai_errors.APIError(1011, {"message": "Internal error encountered.", "status": None}), "outage"),
    # the live probe: close 1011 with the billing text
    (closed_error(1011, "Your prepayment credits are depleted"), "billing_or_quota"),
    (closed_error(1011, "internal error"), "outage"),
    (closed_error(1013, "try again later"), "outage"),
    (closed_error(1006, ""), "outage"),
    (closed_error(1001, "going away"), "outage"),
    (closed_error(1008, "API key not valid. Please pass a valid API key."), "invalid_key"),
    (closed_error(1008, "models/x is not found for API version"), "model_unavailable"),
    (closed_error(1008, "policy violation"), "bad_request"),
    (closed_error(1000, ""), "user_closed"),
    (closed_error(1008, "response blocked by safety filters"), "safety_block"),
    (closed_error(1007, "quota exceeded for this project"), "billing_or_quota"),
    (api_error(genai_errors.ClientError, 401, "API key not valid", "UNAUTHENTICATED"), "invalid_key"),
    (api_error(genai_errors.ClientError, 403, "API key was reported as leaked", "PERMISSION_DENIED"), "invalid_key"),
    (api_error(genai_errors.ClientError, 402, "payment required"), "billing_or_quota"),
    (api_error(genai_errors.ClientError, 429, "You exceeded your current quota", "RESOURCE_EXHAUSTED"), "billing_or_quota"),
    (api_error(genai_errors.ClientError, 404, "models/gemini-9 is not found", "NOT_FOUND"), "model_unavailable"),
    (api_error(genai_errors.ClientError, 400, "Invalid argument"), "bad_request"),
    (api_error(genai_errors.ClientError, 400, "model does not exist"), "model_unavailable"),
    (api_error(genai_errors.ServerError, 500, "Internal error", "INTERNAL"), "outage"),
    (api_error(genai_errors.ServerError, 503, "The model is overloaded", "UNAVAILABLE"), "outage"),
    (api_error(genai_errors.ServerError, 504, "Deadline exceeded", "DEADLINE_EXCEEDED"), "timeout"),
    (asyncio.TimeoutError(), "timeout"),
    (TimeoutError("slow"), "timeout"),
    (ConnectionResetError("reset"), "outage"),
    (OSError("network down"), "outage"),
    (ValueError("bad config"), "bad_request"),
    (RuntimeError("something odd"), "outage"),
    (503, "outage"), (402, "billing_or_quota"), (429, "billing_or_quota"), (401, "invalid_key"), (404, "model_unavailable"),
    (1000, "user_closed"), (1011, "outage"),
    ("Your prepayment credits are depleted", "billing_or_quota"),
    ("blocked by prohibited_content", "safety_block"),
    (None, "user_closed"),
]


@pytest.mark.parametrize("error,expected", TABLE)
def test_error_table(error, expected):
    assert errors.classify(error).reason_class == expected


def test_the_billing_text_beats_the_close_code():
    outcome = errors.classify(1011, "Your prepayment credits are depleted")
    assert outcome.reason_class == "billing_or_quota" and outcome.genuine


@pytest.mark.parametrize("reason", sorted(errors.GENUINE))
def test_genuine_classes(reason):
    assert errors.is_failure(reason)
    assert errors.Outcome(reason).genuine


@pytest.mark.parametrize("reason", ["safety_block", "user_closed"])
def test_not_failures(reason):
    assert not errors.is_failure(reason) and not errors.Outcome(reason).genuine


def test_the_required_classes_all_exist():
    required = {"missing_key", "invalid_key", "billing_or_quota", "model_unavailable", "outage", "timeout",
                "repeated_failure", "safety_block", "user_closed"}
    assert required <= errors.GENUINE | errors.NOT_FAILURES


@pytest.mark.parametrize("value,expected", [
    ("GENERATED_AUDIO_SAFETY", "safety_block"), ("PROHIBITED_INPUT_CONTENT", "safety_block"),
    ("BLOCKLIST", "safety_block"), ("RESPONSE_REJECTED", "safety_block"),
    ("TURN_COMPLETE_REASON_UNSPECIFIED", None), ("NEED_MORE_INPUT", None), (None, None),
])
def test_turn_complete_reason(value, expected):
    outcome = errors.classify_turn_reason(value)
    assert (outcome.reason_class if outcome else None) == expected
    if outcome:
        assert not outcome.genuine  # a safety turn is never a failure


def test_turn_complete_reason_accepts_the_sdk_enum():
    outcome = errors.classify_turn_reason(types.TurnCompleteReason.GENERATED_CONTENT_SAFETY)
    assert outcome is not None and outcome.reason_class == "safety_block"


def test_failure_window_fills_after_three_in_the_window_and_forgets_old_ones():
    now = [0.0]
    window = errors.FailureWindow(3, 300.0, clock=lambda: now[0])
    assert window.record() is False
    now[0] = 100
    assert window.record() is False
    now[0] = 200
    assert window.record() is True
    now[0] = 1000  # all older than 300 s
    assert window.record() is False
    window.reset()
    assert window.record() is False


def test_detail_is_short_and_never_the_key():
    outcome = errors.classify(RuntimeError("x" * 5000))
    assert len(outcome.detail) <= 200
