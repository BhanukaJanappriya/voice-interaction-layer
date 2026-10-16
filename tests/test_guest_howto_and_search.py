"""A new user (guest) gets general how-to answers from the hub's knowledge base, and
personal account questions stay blocked. Also the two search design: the plain question
decides whether the hub knows the topic, a second search only widens the context."""

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

from access import evaluate  # noqa: E402
from tool_agent import knowledge  # noqa: E402

ALLOWED = [
    "how do I pay my bill",
    "How can I pay the bill?",
    "what are the ways to pay my bill",
    "how can I check my balance",
    "how to view my usage",
    "how do I pay online",
]

BLOCKED = [
    "what is my bill",
    "how much is my bill this month",
    "check my balance",
    "why was I charged extra",
    "what is my data usage today",
    "how do I pay my bill and what is my balance",
    "how do I pay my bill, also tell me my last payment",
]


@pytest.mark.parametrize("text", ALLOWED)
def test_how_to_questions_reach_the_hub_for_a_guest(text):
    assert evaluate(text, "en").allowed is True


@pytest.mark.parametrize("text", BLOCKED)
def test_personal_questions_stay_blocked_for_a_guest(text):
    assert evaluate(text, "en").allowed is False


class _Result:
    def __init__(self, chunks, kind="ok", ms=5):
        self.kind = kind
        self.ms = ms
        self.data = {"chunks": chunks}


def _chunk(text, score):
    return {"content": text, "score": score}


def test_the_plain_question_decides_and_the_second_search_adds_context(monkeypatch):
    calls = []

    def fake_call(name, arguments, access, budget=None, confirmed=False):
        calls.append(arguments["query"])
        if arguments["query"].endswith(knowledge.RICH_SUFFIX):
            return _Result([_chunk("rich one " * 5, 0.56), _chunk("shared " * 5, 0.5)])
        if arguments["query"].endswith(knowledge.KEYWORD_SUFFIX):
            return _Result([_chunk("keyword one " * 5, 0.55)])
        return _Result([_chunk("shared " * 5, 0.5)])

    monkeypatch.setattr(knowledge.mcp_gateway, "call", fake_call)
    found = knowledge.search("What fibre packages do you have?", None, budget=4, language="en")
    assert found.kind == "found"
    assert len(calls) == 3
    texts = [c["content"] for c in found.chunks]
    assert texts[0].startswith("rich one"), "the better scored chunk comes first"
    assert any(t.startswith("keyword one") for t in texts)
    assert len(texts) == 3, "the shared chunk is not repeated"


def test_an_off_topic_question_is_not_rescued_by_the_second_search(monkeypatch):
    def fake_call(name, arguments, access, budget=None, confirmed=False):
        if arguments["query"].endswith(knowledge.RICH_SUFFIX):
            return _Result([_chunk("loosely related " * 5, 0.44)])
        return _Result([_chunk("unrelated " * 5, 0.36)])

    monkeypatch.setattr(knowledge.mcp_gateway, "call", fake_call)
    assert knowledge.search("what is the weather today", None, budget=4, language="en").kind == "none"


def test_a_failing_second_search_does_not_cost_the_answer(monkeypatch):
    def fake_call(name, arguments, access, budget=None, confirmed=False):
        if arguments["query"].endswith(knowledge.RICH_SUFFIX):
            raise RuntimeError("hub stalled")
        return _Result([_chunk("plain answer " * 5, 0.6)])

    monkeypatch.setattr(knowledge.mcp_gateway, "call", fake_call)
    found = knowledge.search("tell me about PEOTV", None, budget=4, language="en")
    assert found.kind == "found" and len(found.chunks) == 1


def test_an_unreachable_hub_is_still_reported_as_unavailable(monkeypatch):
    def fake_call(name, arguments, access, budget=None, confirmed=False):
        return _Result([], kind="unavailable")

    monkeypatch.setattr(knowledge.mcp_gateway, "call", fake_call)
    assert knowledge.search("fibre", None, budget=4, language="en").kind == "unavailable"


def test_the_keyword_search_keeps_the_product_word_and_drops_the_filler():
    assert knowledge.keyword_query("Tell me about the YouTube combo package") == "youtube data package price details"
    assert knowledge.keyword_query("do you have a Netflix package?") == "netflix data package price details"
    assert knowledge.keyword_query("What fibre packages do you have?") == "fibre data package price details"
    assert knowledge.keyword_query("what do you have") == ""


def test_the_guest_instruction_asks_for_the_closest_real_item_and_no_websites():
    from tool_agent import messages

    clause = messages.guest_grounded_clause("en")
    assert "closely related" in clause and "exact name is not listed" in clause
    assert "Never send the customer to a website" in clause
