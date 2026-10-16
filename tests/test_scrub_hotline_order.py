"""An invented hotline is removed whatever the word order."""

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

from tool_agent import scrub  # noqa: E402


OFFICIAL = ["1212", "0113121212", "1222"]


@pytest.mark.parametrize("text,removed", [
    ("කිසියම් විමසීම් ඇත්නම්, 1212 හෝ 1717 අමතන්න.", True),
    ("Call 1212 or 1717 for help.", True),
    ("1717 අමතන්න.", True),
    ("Please call 1212, 1222 or 0113121212.", False),
    ("Dial 1212 and 1222.", False),
    ("The package costs 2690 and you can call 1212.", False),
])
def test_an_invented_hotline_is_removed_in_any_word_order(text, removed):
    assert scrub.clean_text(text, "", OFFICIAL)[1] is removed

