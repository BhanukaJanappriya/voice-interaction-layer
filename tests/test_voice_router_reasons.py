"""Which things count as a genuine voice tier failure (plan section 4)."""

import pytest

from voice_router.reasons import classify
from voice_router.types import Failure, NotAFailure


class _Err(Exception):
    def __init__(self, text="", code=None, status_code=None):
        super().__init__(text)
        self.code = code
        self.status_code = status_code


@pytest.mark.parametrize(
    "item, reason",
    [
        ("missing_key", "missing_key"),
        ("invalid_key", "invalid_key"),
        (401, "invalid_key"),
        (403, "invalid_key"),
        (402, "billing_or_quota"),
        (429, "billing_or_quota"),
        ("RESOURCE_EXHAUSTED", "billing_or_quota"),
        ("You exceeded your current quota", "billing_or_quota"),
        (404, "model_unavailable"),
        (500, "outage"),
        (503, "outage"),
        (1011, "outage"),
        (1012, "outage"),
        (1013, "outage"),
        ("timeout", "timeout"),
        ("connect timed out", "timeout"),
        ("repeated_failure", "repeated_failure"),
        (_Err("x", status_code=401), "invalid_key"),
        (_Err("x", code=429), "billing_or_quota"),
        (_Err("x", code=500), "outage"),
        (_Err("x", code=404), "model_unavailable"),
        (TimeoutError("slow"), "timeout"),
        (ConnectionRefusedError("nope"), "outage"),
        (_Err("received 1011 (internal error) Your prepayment credits are depleted", code=1011), "billing_or_quota"),
    ],
)
def test_genuine_failures(item, reason):
    result = classify(item)
    assert isinstance(result, Failure), (item, result)
    assert result.reason == reason


def test_retryable_flags():
    assert classify(500).retryable is True
    assert classify("timeout").retryable is True
    assert classify(402).retryable is False
    assert classify(401).retryable is False


@pytest.mark.parametrize(
    "item",
    [
        "safety_block",
        "SAFETY",
        "prohibited_content",
        "refusal",
        "I don't know that one",
        "user_closed",
        "interrupted",
        "barge_in",
        1000,
        1001,
        None,
        _Err("blocked by safety settings"),
    ],
)
def test_not_failures(item):
    assert isinstance(classify(item), NotAFailure), item


def test_unknown_is_unclassified_not_failure():
    assert classify("something odd").kind == "unclassified"
    assert classify(418).kind == "unclassified"
    assert classify(ValueError("x")).kind == "unclassified"


def test_cause_chain_is_walked():
    try:
        try:
            raise _Err("inner", status_code=429)
        except _Err as inner:
            raise RuntimeError("wrapper") from inner
    except RuntimeError as outer:
        assert classify(outer).reason == "billing_or_quota"


def test_classify_never_raises():
    class Odd:
        def __str__(self):
            raise RuntimeError("boom")

    assert isinstance(classify(Odd()), NotAFailure)
    assert isinstance(classify(object()), NotAFailure)
