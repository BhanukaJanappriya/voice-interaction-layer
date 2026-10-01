"""How retrieved notes are cut down for the model, and which rules go with
them: question aware excerpts, table noise removal, the chunk limit, and
the two prompts (strict with notes, general without)."""

from types import SimpleNamespace

import pytest

from knowledge import prompts, retriever


def _chunk(text, title="Title", url="https://www.mobitel.lk/x", score=0.7):
    return SimpleNamespace(text=text, title=title, source_url=url, retrieved_at="2026-09-30", score=score)


def test_excerpt_returns_short_text_whole():
    assert retriever._excerpt("A short note.", {"note"}, 200) == "A short note."


def test_excerpt_picks_the_sentences_that_match_the_question():
    text = "Intro about the company. " + "Unrelated filler sentence. " * 40 + "Write to info@example.lk for inquiries. " + "More filler. " * 20
    excerpt = retriever._excerpt(text, retriever._query_terms("What email address can I write to for inquiries?"), 200)
    assert "info@example.lk" in excerpt
    assert len(excerpt) <= 210  # the limit plus the ellipsis markers
    assert excerpt.startswith("...")


def test_excerpt_without_matches_keeps_the_start():
    text = "First sentence. " + "Another sentence. " * 50
    assert retriever._excerpt(text, {"zzz"}, 100).startswith("First sentence.")


def test_query_terms_drop_filler_words():
    terms = retriever._query_terms("How much is the 7 day YouTube Combo for Mobitel?")
    assert {"day", "youtube", "combo"} <= terms
    assert not ({"the", "how", "much", "mobitel"} & terms)


def test_format_context_removes_table_noise():
    context = retriever.format_context(
        [_chunk("Package: X; Price: Rs.191; Data: 1GB, Self-care App, View Charges/ More Info, TRC Code - TRC/M/PRO/24/02;")],
        "price",
    )
    assert "Rs.191" in context
    assert "Self-care" not in context and "View Charges" not in context and "TRC/M" not in context


def test_format_context_uses_at_most_max_chunks_and_names_sources():
    results = [_chunk(f"Note number {n}.", title=f"T{n}") for n in range(6)]
    context = retriever.format_context(results, "note")
    assert context.count("https://www.mobitel.lk/x") == retriever.MAX_CHUNKS
    assert "2026-09-30" in context


def test_rules_are_strict_with_notes_and_general_without():
    assert prompts.rules_for("some notes") is prompts.GROUNDED_RULES
    assert prompts.rules_for(None) is prompts.GENERAL_RULES
    assert "only from the SLT-Mobitel notes" in prompts.GROUNDED_RULES
    assert "own knowledge" in prompts.GENERAL_RULES


def test_with_context_adds_notes_only_when_found():
    with_notes = prompts.with_context("PERSONA", "NOTES BLOCK")
    assert with_notes.startswith("PERSONA") and with_notes.endswith("NOTES BLOCK")
    assert prompts.GROUNDED_RULES in with_notes and prompts.GENERAL_RULES not in with_notes
    without = prompts.with_context("PERSONA", None)
    assert prompts.GENERAL_RULES in without and prompts.GROUNDED_RULES not in without


def test_general_rules_never_allow_inventing_sltmobitel_specifics():
    for needle in ("price", "USSD", "email", "never make those up"):
        assert needle in prompts.GENERAL_RULES


@pytest.mark.parametrize("rules", [prompts.GROUNDED_RULES, prompts.GENERAL_RULES])
def test_rules_point_to_the_official_channels(rules):
    assert "www.sltmobitel.lk" in rules and "1717" in rules
