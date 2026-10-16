"""A polite answer to a general question, the hub only for SLT questions, and plain friendly wording."""

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

import config  # noqa: E402
import tool_agent  # noqa: E402
from tool_agent import knowledge, messages  # noqa: E402
from tool_agent.plan import CANNED, GROUNDED, PASS  # noqa: E402
from tool_agent.topic import is_slt_topic  # noqa: E402

SLT_QUESTIONS = [
    "What fibre packages do you have?", "Tell me about the YouTube combo package", "how do I pay my bill",
    "my router is not working", "SLT hotline number", "Do you have a Netflix package", "I want a new connection",
    "roaming charges in India", "ඔබේ ෆයිබර් පැකේජ මොනවාද", "பேக்கேஜ் விலை என்ன", "data eka kiyada",
    "Is there coverage in Kandy?", "what is my balance",
]
GENERAL_QUESTIONS = [
    "What is the capital of France?", "who won the world cup in 2018", "how do I cook rice", "what is 123 plus 5",
    "tell me a joke", "explain photosynthesis", "Who invented the telephone?", "ප්‍රංශයේ අගනුවර කුමක්ද",
    "பிரான்சின் தலைநகரம் என்ன", "how many days are in a leap year", "Can you tell me a fun fact about elephants?",
]


@pytest.mark.parametrize("text", SLT_QUESTIONS)
def test_slt_questions_are_recognised(text):
    assert is_slt_topic(text) is True


@pytest.mark.parametrize("text", GENERAL_QUESTIONS)
def test_general_questions_are_recognised(text):
    assert is_slt_topic(text) is False


@pytest.fixture
def hub_mode(monkeypatch):
    monkeypatch.setattr(config, "ANSWER_SOURCE", "mcp_hub")
    monkeypatch.setattr(config, "ACCESS_CONTROL_ENABLED", True)


def search_returns(monkeypatch, kind, chunks=None):
    monkeypatch.setattr(knowledge, "search", lambda *a, **k: knowledge.Search(kind, chunks or [], 5))


@pytest.mark.parametrize("kind", ["none", "unavailable"])
def test_a_question_that_is_not_about_slt_gets_a_polite_decline_and_no_model(hub_mode, monkeypatch, kind):
    search_returns(monkeypatch, kind)
    plan = tool_agent.prepare("What is the capital of France?", "en", None)
    assert plan.kind == CANNED and plan.context == ""
    assert "only help with SLT-Mobitel services" in plan.canned and "1212" in plan.canned


def test_an_slt_question_the_hub_knows_nothing_about_gets_the_fixed_dial_text(hub_mode, monkeypatch):
    search_returns(monkeypatch, "none")
    plan = tool_agent.prepare("Tell me about the zebra tariff", "en", None)
    assert plan.kind == CANNED and "1212" in plan.canned


def test_an_slt_question_with_the_hub_down_gets_the_hub_unavailable_text(hub_mode, monkeypatch):
    search_returns(monkeypatch, "unavailable")
    plan = tool_agent.prepare("What fibre packages do you have?", "en", None)
    assert plan.kind == CANNED


def test_an_slt_question_the_hub_answers_is_grounded_in_the_hub(hub_mode, monkeypatch):
    search_returns(monkeypatch, "found", [{"content": "Fiber Starter Rs. 2,690 80 GB", "score": 0.6}])
    plan = tool_agent.prepare("What fibre packages do you have?", "en", None)
    assert plan.kind == GROUNDED and "Fiber Starter" in plan.context and plan.tools == ("search_knowledgebase_tool",)


def test_hub_answers_are_asked_to_be_friendly_plain_and_organised():
    clause = messages.guest_grounded_clause("en")
    assert "customer care officer" in clause and "plain text only" in clause.lower()
    assert "no asterisks" in clause and "never field names" in clause
    assert "closely related" in clause and "Never send the customer to a website" in clause
    assert "ONLY from the SLT excerpts" in clause


def test_small_talk_is_also_in_a_caring_voice():
    assert "customer care officer" in messages.no_facts_clause("en")


@pytest.mark.parametrize("text", ["What is fibre internet?", "how does roaming work", "What is the price of fibre",
                                  "what are your fibre packages", "Tell me about the zebra tariff"])
def test_slt_topics_with_nothing_in_the_hub_still_get_the_fixed_text(hub_mode, monkeypatch, text):
    search_returns(monkeypatch, "none")
    plan = tool_agent.prepare(text, "en", None)
    assert plan.kind == CANNED and "could not find that in the SLT information" in plan.canned


def test_a_hub_answer_never_adds_general_knowledge_or_other_sources():
    clause = messages.guest_grounded_clause("en")
    assert "politely decline in one short sentence" in clause and "never answer it from memory" in clause
    assert "general knowledge" in clause  # only as an example of what is declined
    assert "helpful general knowledge" not in clause
    assert "ONLY from the SLT excerpts" in clause
