"""Gemini Live speech to speech session (backend proxy side).

    from gemini_live import GeminiLiveSession, LiveSessionConfig
    from gemini_live import build_instruction, build_tools, ToolExecutor, classify

See session.py for the event classes and the module texts for the contracts.
The real key has to be funded: no part of this was run against the live API.
"""

from .config import DEFAULT_MODEL, LiveSessionConfig
from .errors import GENUINE, NOT_FAILURES, Outcome, classify, is_failure
from .instructions import build_instruction
from .session import (
    AudioOut,
    Failed,
    GeminiLiveSession,
    GoAway,
    InputText,
    Interrupted,
    OutputText,
    Resumed,
    ToolActivity,
    TurnComplete,
)
from .tools import ToolExecutor, build_tools, declarations, make_executor

__all__ = [
    "GeminiLiveSession", "LiveSessionConfig", "DEFAULT_MODEL",
    "AudioOut", "InputText", "OutputText", "Interrupted", "TurnComplete", "ToolActivity", "GoAway", "Resumed", "Failed",
    "build_instruction", "build_tools", "declarations", "ToolExecutor", "make_executor",
    "classify", "Outcome", "is_failure", "GENUINE", "NOT_FAILURES",
]
