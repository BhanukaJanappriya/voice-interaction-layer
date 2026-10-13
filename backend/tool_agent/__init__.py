"""Grounded answering: every SLT answer is built from hub tool results.

    from tool_agent import prepare
    plan = prepare(text, language, access, user_name)     # blocking, never raises
    plan.kind: "pass" | "canned" | "grounded"

See core.py for the decisions and why tools are called deterministically,
voice.py for the spoken wording, schemas.py for a future cloud function calling
path and stats.py for the numbers shown in /health.
"""

from .core import prepare
from .plan import CANNED, GROUNDED, PASS, TurnPlan
from .schemas import schemas_for
from .smalltalk import is_smalltalk
from .stats import reset as reset_stats
from .stats import stats
from .voice import for_speech, shorten_for_voice

__all__ = [
    "prepare", "TurnPlan", "PASS", "CANNED", "GROUNDED", "schemas_for", "stats", "for_speech", "shorten_for_voice",
    "is_smalltalk", "reset_stats",
]
